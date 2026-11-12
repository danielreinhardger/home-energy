"""Daily and monthly energy money ledger (pure logic; persisted in the SQLite store, `days` table).

Every poll stores today's running day totals (they only grow during a day; the
devices reset them at midnight), so the last value of a day becomes its final value.
Each day also keeps the prices valid on that day, so a later price change never
rewrites the past.

Per day: grid import/export (openWB meter), PV yield (Kostal), house and car
consumption (openWB), the heat pump's counters, the electric backup heater's kWh (its heat
is its electricity; it is already inside house consumption and grid import, so the money below
needs no correction – the field only makes it visible) and the resulting money:
  cost      = import × price_buy
  earnings  = export × price_feed
  net       = earnings − cost
  without_pv = (house + car) × price_buy      what the same consumption would cost without PV
  benefit   = without_pv + net                what PV and battery brought that day
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date

from .store import Store

log = logging.getLogger(__name__)


@dataclass
class DayRecord:
    day: str                       # ISO date
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    pv_kwh: float = 0.0
    house_kwh: float = 0.0
    car_kwh: float = 0.0
    hp_heat_heating_kwh: float = 0.0     # heat pump counters (ISG page, Wh resolution)
    hp_power_heating_kwh: float = 0.0
    hp_heat_dhw_kwh: float = 0.0
    hp_power_dhw_kwh: float = 0.0
    backup_kwh: float = 0.0              # electric backup heater (Heizstab), heating + hot water
    outside_sum: float = 0.0             # running mean of the outside temperature (for COP calibration)
    outside_n: int = 0
    price_buy: float = 0.35
    price_feed: float = 0.092

    @property
    def cost_eur(self) -> float:
        return self.import_kwh * self.price_buy

    @property
    def earn_eur(self) -> float:
        return self.export_kwh * self.price_feed

    @property
    def net_eur(self) -> float:
        return self.earn_eur - self.cost_eur

    @property
    def without_pv_eur(self) -> float:
        return (self.house_kwh + self.car_kwh) * self.price_buy

    @property
    def benefit_eur(self) -> float:
        return self.without_pv_eur + self.net_eur

    @property
    def outside_avg(self) -> float | None:
        return round(self.outside_sum / self.outside_n, 1) if self.outside_n else None

    @property
    def cop_heating(self) -> float | None:
        return _cop(self.hp_heat_heating_kwh, self.hp_power_heating_kwh)

    @property
    def cop_dhw(self) -> float | None:
        return _cop(self.hp_heat_dhw_kwh, self.hp_power_dhw_kwh)

    def to_json(self) -> dict:
        d = asdict(self)
        d.update(cop_heating=self.cop_heating, cop_dhw=self.cop_dhw, outside_avg=self.outside_avg)
        d.pop("outside_sum"); d.pop("outside_n")
        d.update(cost_eur=round(self.cost_eur, 3), earn_eur=round(self.earn_eur, 3), net_eur=round(self.net_eur, 3),
                 without_pv_eur=round(self.without_pv_eur, 3), benefit_eur=round(self.benefit_eur, 3))
        return d


SUM_FIELDS = ("import_kwh", "export_kwh", "pv_kwh", "house_kwh", "car_kwh",
              "hp_heat_heating_kwh", "hp_power_heating_kwh", "hp_heat_dhw_kwh", "hp_power_dhw_kwh", "backup_kwh")


def _cop(heat: float, power: float) -> float | None:
    """COP of a day/month – only when the heat pump really heated (≥ 0.5 kWh electricity and
    more heat than electricity; standby and circulation pump alone give meaningless ratios)."""
    if power >= 0.5 and heat >= power:
        return round(heat / power, 2)
    return None


MONEY_FIELDS = ("cost_eur", "earn_eur", "net_eur", "without_pv_eur", "benefit_eur")


def day_record(d: dict) -> DayRecord | None:
    names = DayRecord.__dataclass_fields__.keys()
    try:
        return DayRecord(**{k: v for k, v in d.items() if k in names})
    except TypeError:
        return None


class Ledger:
    """All days in memory (a few KB per year); changed days are written to the store."""

    def __init__(self, store: Store):
        self.store = store
        self.days: dict[str, DayRecord] = {}
        self._dirty: set[str] = set()
        self.last_raw: dict[str, float] = {k: float(v) for k, v in (store.get_meta("last_raw") or {}).items()}
        for key, d in store.load_days().items():
            rec = day_record(d)
            if rec:
                self.days[key] = rec

    def save(self) -> None:
        try:
            self.store.save_days({k: asdict(self.days[k]) for k in self._dirty if k in self.days})
            self.store.set_meta("last_raw", self.last_raw)
            self._dirty.clear()
        except Exception as exc:                      # sqlite3.Error, OSError
            log.warning("finance ledger: %s", exc)

    def update(self, day: date, totals: dict, price_buy: float, price_feed: float,
               allow_decrease: bool = False, hour: int | None = None) -> bool:
        """Store today's running totals. Values only grow during a day; a smaller value
        (a stale reading) never overwrites a bigger one, with two exceptions:
        - right after midnight (`allow_decrease`), when a device may still report yesterday's total;
        - a counter that drops between 00:00 and 04:00 (`hour`): the device reset its own day
          counter on its own clock (the ISG resets about an hour late), so what today's record
          held until then was still yesterday's total."""
        key = day.isoformat()
        rec = self.days.get(key) or DayRecord(key)
        changed = key not in self.days
        for f in SUM_FIELDS:
            v = totals.get(f)
            if v is None:
                continue
            v = round(float(v), 3)
            last = self.last_raw.get(f)
            device_reset = hour is not None and hour < 4 and last is not None and v < last - 1e-3
            cur = getattr(rec, f)
            if v > cur + 1e-6 or ((allow_decrease or device_reset) and abs(v - cur) > 1e-6):
                setattr(rec, f, v)
                changed = True
            self.last_raw[f] = v
        if (rec.price_buy, rec.price_feed) != (price_buy, price_feed):
            rec.price_buy, rec.price_feed = price_buy, price_feed     # price valid on that day
            changed = True
        self.days[key] = rec
        if changed:
            self._dirty.add(key)
        return changed

    def add_outside(self, day: date, temp: float | None) -> None:
        """Fold one outside temperature reading into the day's average."""
        if temp is None:
            return
        rec = self.days.setdefault(day.isoformat(), DayRecord(day.isoformat()))
        rec.outside_sum += float(temp)
        rec.outside_n += 1
        self._dirty.add(rec.day)

    def heating_days(self, days: int = 14, today: str | None = None) -> list[tuple[float, float, float]]:
        """(outside_avg, heat_kwh, power_kwh) of recent finished days with real heating."""
        out = []
        for k in [k for k in sorted(self.days) if k != today][-days:]:
            r = self.days[k]
            if r.cop_heating is not None and r.outside_avg is not None and r.hp_power_heating_kwh >= 1.0:
                out.append((r.outside_avg, r.hp_heat_heating_kwh, r.hp_power_heating_kwh))
        return out

    def day_rows(self, limit: int = 62) -> list[dict]:
        return [r.to_json() for r in sorted(self.days.values(), key=lambda r: r.day, reverse=True)[:limit]]

    def month_rows(self) -> list[dict]:
        months: dict[str, dict] = {}
        for r in self.days.values():
            m = months.setdefault(r.day[:7], {"month": r.day[:7], "days": 0, **{f: 0.0 for f in SUM_FIELDS + MONEY_FIELDS}})
            m["days"] += 1
            for f in SUM_FIELDS:
                m[f] += getattr(r, f)
            for f in MONEY_FIELDS:
                m[f] += getattr(r, f)
        rows = sorted(months.values(), key=lambda m: m["month"], reverse=True)
        for m in rows:
            for f in SUM_FIELDS + MONEY_FIELDS:
                m[f] = round(m[f], 2)
            m["cop_heating"] = _cop(m["hp_heat_heating_kwh"], m["hp_power_heating_kwh"])
            m["cop_dhw"] = _cop(m["hp_heat_dhw_kwh"], m["hp_power_dhw_kwh"])
        return rows

    def recent_hp(self, days: int = 14, today: str | None = None) -> dict:
        """Heat pump heat/power sums of the last finished days (for the COP model)."""
        keys = [k for k in sorted(self.days) if k != today][-days:]
        return {"heat_dhw_kwh": sum(self.days[k].hp_heat_dhw_kwh for k in keys),
                "power_dhw_kwh": sum(self.days[k].hp_power_dhw_kwh for k in keys),
                "heat_heating_kwh": sum(self.days[k].hp_heat_heating_kwh for k in keys),
                "power_heating_kwh": sum(self.days[k].hp_power_heating_kwh for k in keys)}

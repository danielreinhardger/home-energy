"""Day simulation: the real strategy code against simple models of house, tank and battery.

Not a forecast of the real site – a check that the rules do what the economics say.
Each minute: PV and house load come from a profile, the controllers (the same
decide_heating()/decide() the service uses) set the targets, a thermostat model runs
the heat pump (hot water has priority, as in the LWZ), the battery takes or gives the
rest and the grid balances. Baseline = the old fixed targets (room 23.5 °C, hot water 46 °C).

Simplifications (documented on purpose): targets act immediately (the real controller
writes at most every 10 min); the inverter output follows the distance target–room
linearly (a stand-in for the LWZ's own curve); no solar gains through windows; constant outside temperature.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .cop import HEAT_OUTPUT_KW, CopModel
from .economics import EconomyConfig
from .forecast import ForecastSummary
from .heating import HeatingConfig, HeatingInputs, HeatState, decide_heating
from .hotwater import DhwConfig, DhwInputs, DhwState, decide
from .situation import Situation

TZ = timezone(timedelta(hours=1))


@dataclass
class Scenario:
    name: str
    pv_peak_kw: float                 # sunny winter day ~4.5 kW on this roof in November
    outside_c: float
    pv_peak_tomorrow_kw: float | None = None
    sun_from: float = 8.0             # hour of day
    sun_to: float = 16.0
    # house (estimates for the KfW40+ VIO, see docs/strategy.md)
    loss_kw_per_k: float = 0.06
    capacity_kwh_per_k: float = 20.0
    heat_kw: float = HEAT_OUTPUT_KW   # LWZ 5 S Plus datasheet heating output
    cop_heating: float | None = None  # None = from the COP model at the outside temperature
    # hot water
    tank_l: float = 235.0
    dhw_kw: float = 3.4               # heat output in hot water mode
    cop_dhw: float = 2.6
    draws: tuple = ((7.0, 3.0), (19.0, 2.0))     # (hour, kWh heat drawn)
    # electricity
    base_load_kw: float = 0.35
    evening_load_kw: float = 0.8      # 18–22 h devices
    battery_kwh: float = 10.24
    soc_start: float = 0.3


@dataclass
class Result:
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    hp_kwh_day: float = 0.0           # heat pump electricity while the sun shines
    hp_kwh_night: float = 0.0
    room_min: float = 99.0
    room_max: float = 0.0
    dhw_min: float = 99.0
    log: list = field(default_factory=list)

    def money(self, eco: EconomyConfig) -> float:
        """Net cost of the day in € (positive = we pay)."""
        return self.import_kwh * eco.price_buy - self.export_kwh * eco.price_feed


def pv_kw(s: Scenario, hour: float, peak: float) -> float:
    if not s.sun_from <= hour <= s.sun_to:
        return 0.0
    return peak * math.sin(math.pi * (hour - s.sun_from) / (s.sun_to - s.sun_from))


def _forecast(s: Scenario, now: datetime, peak_today: float, peak_tomorrow: float) -> ForecastSummary:
    hour = now.hour + now.minute / 60
    rest = sum(pv_kw(s, h / 4, peak_today) for h in range(int(hour * 4), 96)) / 4 * 1000
    total = lambda p: sum(pv_kw(s, h / 4, p) for h in range(96)) / 4 * 1000
    sun_left = max(0.0, s.sun_to - max(hour, s.sun_from)) if peak_today > 0.3 else 0.0
    return ForecastSummary(now, 1.0, rest, total(peak_today), total(peak_tomorrow), sun_left, [])


def run(s: Scenario, strategy: bool, days: int = 2, eco: EconomyConfig | None = None,
        dcfg: DhwConfig | None = None, hcfg: HeatingConfig | None = None, cop: CopModel | None = None) -> Result:
    """Simulate `days` days; the result covers the last day (the first one settles the state)."""
    eco = eco or EconomyConfig()
    dcfg, hcfg = dcfg or DhwConfig(), hcfg or HeatingConfig()
    from dataclasses import replace
    dcfg, hcfg = replace(dcfg, enabled=True), replace(hcfg, enabled=True)   # the model always runs both
    cop = cop or CopModel(heating_scale=1.11, dhw_cop=s.cop_dhw)
    cop_heating = s.cop_heating or cop.heating(s.outside_c)
    room, tank, soc = hcfg.normal_temp, dcfg.comfort_temp, s.soc_start * s.battery_kwh
    dstate, hstate = DhwState(), HeatState()
    room_target, dhw_target = hcfg.normal_temp, dcfg.comfort_temp
    hp_heat_on = hp_dhw_on = False
    grid_w = battery_w = 0.0
    tank_kwh_per_k = s.tank_l * 1.163 / 1000
    start = datetime(2026, 1, 14, 0, 0, tzinfo=TZ)
    res = Result()
    for minute in range(days * 24 * 60):
        now = start + timedelta(minutes=minute)
        hour = now.hour + now.minute / 60
        last_day = minute >= (days - 1) * 24 * 60
        peak = s.pv_peak_kw
        peak_tomorrow = s.pv_peak_tomorrow_kw if s.pv_peak_tomorrow_kw is not None else peak
        pv = pv_kw(s, hour, peak)

        # --- controllers (same code as the service) ---------------------------------
        if strategy:
            # the strategies only know the configured base load, not the model's true one
            sit = Situation(now, pv_w=pv * 1000, grid_w=grid_w, battery_w=battery_w, soc=soc / s.battery_kwh * 100,
                            battery_capacity_wh=s.battery_kwh * 1000, outside_temp=s.outside_c, room_temp=room,
                            dhw_temp=tank, forecast=_forecast(s, now, peak, peak_tomorrow), base_load_w=dcfg.base_load_w)
            hd, hstate = decide_heating(HeatingInputs(sit, dstate.mode.value, cop_heating), hcfg, hstate)
            dd, dstate = decide(DhwInputs(sit, heating_season=s.outside_c < hcfg.outside_max,
                                          heating_boost=hd.mode.value == "boost"), dcfg, dstate)
            room_target, dhw_target = hd.target, dd.target

        # --- heat pump: hot water first, 4 K hysteresis; heating ±0.1 K, the inverter modulates
        #     with the distance target–room (25 % at a small gap, full power from 1 K) ----------
        if tank < dhw_target - 4:
            hp_dhw_on = True
        elif tank >= dhw_target:
            hp_dhw_on = False
        if s.outside_c < hcfg.outside_max:
            if room < room_target - 0.1:
                hp_heat_on = True
            elif room >= room_target + 0.1:
                hp_heat_on = False
        else:
            hp_heat_on = False
        cop_dhw = cop.dhw(tank, 46.0)
        hp_kw = 0.0
        if hp_dhw_on:
            hp_kw = s.dhw_kw / cop_dhw
            tank += s.dhw_kw / 60 / tank_kwh_per_k
        elif hp_heat_on:
            share = min(1.0, max(0.25, room_target + 0.1 - room))
            hp_kw = share * s.heat_kw / cop_heating
            room += share * s.heat_kw / 60 / s.capacity_kwh_per_k
        room -= s.loss_kw_per_k * (room - s.outside_c) / 60 / s.capacity_kwh_per_k
        tank -= 0.0015 * (tank - 20) / 60 / tank_kwh_per_k            # standing loss ~1.5 W/K
        for at, kwh in s.draws:
            if abs(hour - at) < 1 / 120:
                tank -= kwh / tank_kwh_per_k

        # --- electricity: battery takes/gives the rest, grid balances ---------------------
        load = s.base_load_kw + (s.evening_load_kw if 18 <= hour < 22 else 0.0) + hp_kw
        net = pv - load                                               # + surplus / − deficit
        if net >= 0:
            charge = min(net, 5.0, (s.battery_kwh - soc) * 60 / 0.95)
            soc += charge * 0.95 / 60
            export, imp, battery_w = net - charge, 0.0, -charge * 1000
        else:
            discharge = min(-net, 5.0, max(0.0, soc - 0.05 * s.battery_kwh) * 60 * 0.95)
            soc -= discharge / 0.95 / 60
            export, imp, battery_w = 0.0, -net - discharge, discharge * 1000
        grid_w = (imp - export) * 1000

        if last_day:
            res.import_kwh += imp / 60
            res.export_kwh += export / 60
            if pv > 0.05:
                res.hp_kwh_day += hp_kw / 60
            else:
                res.hp_kwh_night += hp_kw / 60
            res.room_min, res.room_max = min(res.room_min, room), max(res.room_max, room)
            res.dhw_min = min(res.dhw_min, tank)
            if now.minute == 0:
                res.log.append((now.hour, round(room, 2), round(tank, 1), round(soc / s.battery_kwh * 100), room_target, dhw_target))
    return res


SCENARIOS = (
    Scenario("Sonniger Wintertag (5 °C)", pv_peak_kw=4.5, outside_c=5.0),
    Scenario("Trüber Wintertag, morgen Sonne", pv_peak_kw=1.2, outside_c=3.0, pv_peak_tomorrow_kw=4.5),
    Scenario("Kalter Sonnentag (−5 °C)", pv_peak_kw=4.5, outside_c=-5.0),
    Scenario("Sommertag", pv_peak_kw=8.0, outside_c=20.0, sun_from=6.0, sun_to=20.0, soc_start=0.5),
)


def compare(eco: EconomyConfig | None = None, dcfg: DhwConfig | None = None,
            hcfg: HeatingConfig | None = None, cop: CopModel | None = None) -> list[dict]:
    """Baseline (fixed targets) vs. strategy for each scenario, with the given (live) settings."""
    eco = eco or EconomyConfig()
    rows = []
    for s in SCENARIOS:
        base = run(s, strategy=False, eco=eco, dcfg=dcfg, hcfg=hcfg, cop=cop)
        new = run(s, strategy=True, eco=eco, dcfg=dcfg, hcfg=hcfg, cop=cop)
        rows.append({
            "scenario": s.name,
            "baseline_eur": round(base.money(eco), 2), "strategy_eur": round(new.money(eco), 2),
            "saving_eur": round(base.money(eco) - new.money(eco), 2),
            "hp_night_kwh": (round(base.hp_kwh_night, 2), round(new.hp_kwh_night, 2)),
            "import_kwh": (round(base.import_kwh, 1), round(new.import_kwh, 1)),
            "export_kwh": (round(base.export_kwh, 1), round(new.export_kwh, 1)),
            "room_range": (round(new.room_min, 1), round(new.room_max, 1)),
            "dhw_min": (round(base.dhw_min, 1), round(new.dhw_min, 1)),
            "cop_heating": round(cop.heating(s.outside_c) if cop else CopModel(1.11).heating(s.outside_c), 2),
        })
    return rows


if __name__ == "__main__":
    for r in compare():
        print(r)

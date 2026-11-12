"""Heat pump efficiency model for the Stiebel Eltron LWZ 5 S Plus (pure, no I/O).

Heating COP depends on the outside temperature. The shape comes from the datasheet
(EN 14511, flow 35 °C – floor heating): A−7/W35 2.61, A2/W35 3.74, A7/W35 4.74,
heat output 4.4 kW. The level is calibrated to this house's own counters: the lifetime
COP (WM Heizen / P Heizen) is compared with the datasheet curve at a typical
heating-season temperature, and the curve is scaled by that ratio (0.8–1.4).

Hot water COP comes from the counters (lifetime or recent days) and drops ~3 % per K
above the comfort temperature (higher condensing temperature).

Sources: STIEBEL ELTRON tender text / product flyer LWZ 5 S Plus (heinze.de, stiebel-eltron.de).
"""
from __future__ import annotations

from .i18n import fnum, t

from dataclasses import dataclass, field

DATASHEET_HEATING = ((-7.0, 2.61), (2.0, 3.74), (7.0, 4.74))    # (outside °C, COP at W35)
HEAT_OUTPUT_KW = 4.4
REFERENCE_SEASON_TEMP = 4.0          # typical heating-season outside temperature for calibration
COP_DROP_PER_K = 0.03


def datasheet_cop(outside_c: float) -> float:
    """Linear interpolation through the datasheet points, clamped 1.8–6. Beyond the last point
    (no data above 7 °C) the curve rises at only half the slope – deliberately conservative."""
    pts = DATASHEET_HEATING
    if outside_c <= pts[0][0]:
        (t0, c0), (t1, c1) = pts[0], pts[1]
        cop = c0 + (c1 - c0) * (outside_c - t0) / (t1 - t0)
    elif outside_c >= pts[-1][0]:
        (t0, c0), (t1, c1) = pts[-2], pts[-1]
        cop = c1 + 0.5 * (c1 - c0) / (t1 - t0) * (outside_c - t1)
    else:
        (t0, c0), (t1, c1) = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] <= outside_c <= b[0])
        cop = c0 + (c1 - c0) * (outside_c - t0) / (t1 - t0)
    return max(1.8, min(6.0, cop))


@dataclass(frozen=True)
class LiveFit:
    """Live heating COP (compressor only, FACHMANN page) as a line over the outside temperature.
    Only its *ratio* between two temperatures is used: fans and pumps, which the motor power
    leaves out, shift the level but hardly the ratio."""
    a: float
    per_k: float                     # COP change per K
    minutes: int
    t_lo: float
    t_hi: float

    def ratio(self, t_to: float, t_from: float) -> float | None:
        """fit(t_to) / fit(t_from) – only inside the measured range (± 1 K), else None."""
        lo, hi = self.t_lo - 1, self.t_hi + 1
        if not (lo <= t_to <= hi and lo <= t_from <= hi):
            return None
        a, b = self.a + self.per_k * t_to, self.a + self.per_k * t_from
        return a / b if a > 0 and b > 0 else None


LIVE_MIN_MINUTES = 120
LIVE_MIN_SPAN_K = 4.0


def fit_live(points: list[tuple[float, float]]) -> LiveFit | None:
    """Least squares through (outside °C, live heating COP) minutes. None while there is too little
    data: fewer than 2 h of heating, or outside temperatures spanning less than 4 K. The slope is
    kept to 0…0.3 per K (a heat pump does not get worse in warmer air)."""
    pts = [(t, c) for t, c in points if t is not None and c is not None]
    if len(pts) < LIVE_MIN_MINUTES:
        return None
    ts = sorted(t for t, _ in pts)
    lo, hi = ts[len(ts) // 50], ts[-1 - len(ts) // 50]              # ignore the outer 2 %
    if hi - lo < LIVE_MIN_SPAN_K:
        return None
    n = len(pts)
    mt, mc = sum(t for t, _ in pts) / n, sum(c for _, c in pts) / n
    var = sum((t - mt) ** 2 for t, _ in pts)
    b = max(0.0, min(0.3, sum((t - mt) * (c - mc) for t, c in pts) / var))
    return LiveFit(round(mc - b * mt, 3), round(b, 4), n, round(lo, 1), round(hi, 1))


@dataclass
class CopModel:
    heating_scale: float = 1.0       # measured / datasheet
    dhw_cop: float = 2.6             # at comfort temperature
    source: str = field(default_factory=lambda: t("cop.datenblatt"))
    live: LiveFit | None = None      # measured shape (only for the day/night ratio)

    def heating(self, outside_c: float | None) -> float:
        t = REFERENCE_SEASON_TEMP if outside_c is None else outside_c
        return datasheet_cop(t) * self.heating_scale

    def dhw(self, tank_temp: float | None = None, comfort: float = 46.0) -> float:
        extra = 0.0 if tank_temp is None else max(0.0, tank_temp - comfort)
        return self.dhw_cop * max(0.3, 1 - COP_DROP_PER_K * extra)

    def day_gain(self, t_min: float | None, t_max: float | None, cap: float = 0.25) -> float | None:
        """How much better the COP is in the PV hours than at night (e.g. 0.12 = +12 %).
        Uses temperatures 1 K inside the day's min/max (heating doesn't happen exactly at the
        extremes) and caps the result – the datasheet curve is only a guide."""
        if t_min is None or t_max is None:
            return None
        night, day = t_min + 1, max(t_min + 1, t_max - 1)
        measured = self.live.ratio(day, night) if self.live else None
        ratio = measured if measured is not None else self.heating(day) / self.heating(night)
        return max(0.0, min(cap, ratio - 1))

    def day_gain_measured(self, t_min: float | None, t_max: float | None) -> bool:
        """Does day_gain() come from the live measurements (both temperatures in their range)?"""
        if self.live is None or t_min is None or t_max is None:
            return False
        return self.live.ratio(max(t_min + 1, t_max - 1), t_min + 1) is not None


MIN_HEATING_DAYS = 3


def calibrate(isg: dict, recent: dict | None = None, fallback_heating: float = 4.6,
              fallback_dhw: float = 2.6, heating_days: list | None = None,
              live: LiveFit | None = None) -> CopModel:
    """Build the model from the heat pump counters.

    `isg`: Modbus values (lifetime heat/power totals). `recent`: optional sums of recent
    days from the ledger (heat/power per mode) – preferred for hot water once there is
    enough data, because the tank temperatures the strategy uses differ from the past.
    """
    def ratio(heat, power, lo, hi):
        if heat and power and power > 5:
            r = heat / power
            if lo <= r <= hi:
                return r
        return None

    lifetime_h = ratio(isg.get("heat_heating_total"), isg.get("power_heating_total"), 1.5, 7)
    scale, src = 1.0, t("cop.datenblatt")
    days = [d for d in (heating_days or []) if d[2] >= 1.0]
    if len(days) >= MIN_HEATING_DAYS:
        # measured days: compare each day's COP with the datasheet at that day's outside temperature,
        # weighted by how much the heat pump ran
        expected = sum(power * datasheet_cop(temp) for temp, _, power in days)
        scale = max(0.6, min(1.4, sum(heat for _, heat, _ in days) / expected))
        src = t("cop.datenblatt_gemessen_heiztage", scale=fnum(scale, 2), value=len(days))
    elif lifetime_h:
        scale = max(0.8, min(1.4, lifetime_h / datasheet_cop(REFERENCE_SEASON_TEMP)))
        src = t("cop.datenblatt_zaehler", scale=fnum(scale, 2))
    elif fallback_heating:
        scale = max(0.8, min(1.4, fallback_heating / datasheet_cop(REFERENCE_SEASON_TEMP)))

    dhw = None
    if recent:
        dhw = ratio(recent.get("heat_dhw_kwh"), recent.get("power_dhw_kwh"), 1.2, 5)
        if dhw and (recent.get("power_dhw_kwh") or 0) < 10:      # too little data yet
            dhw = None
        if dhw:
            src += t("cop.ww_letzte_tage")
    if dhw is None:
        dhw = ratio(isg.get("heat_dhw_total"), isg.get("power_dhw_total"), 1.2, 5) or fallback_dhw
    return CopModel(scale, dhw, src, live)

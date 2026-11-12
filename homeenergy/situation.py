"""The energy situation at one moment – computed once, used by every strategy.

Both strategies (heating, hot water) and the simulation need the same derived values:
surplus, export, deficit, what the battery still needs, how much PV is still free
today, whether the battery will fill anyway, whether the sun is up. They used to be
computed separately in several places, and the copies drifted apart. Now they are
properties of one `Situation`, built by `from_devices()` in the service and directly
in the simulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .forecast import ForecastSummary


@dataclass(frozen=True)
class Situation:
    now: datetime
    pv_w: float = 0.0
    grid_w: float = 0.0                       # + import / − export
    battery_w: float = 0.0                    # + discharge / − charge
    soc: float = 0.0                          # %
    max_soc: float = 100.0                    # % the battery charges to
    battery_capacity_wh: float = 10240.0
    outside_temp: float | None = None
    room_temp: float | None = None            # living room, from the room sensor (None = no valid reading)
    dhw_temp: float | None = None             # hot water tank
    compressor_w: float | None = None         # live compressor power (ISG FACHMANN page)
    forecast: ForecastSummary | None = None
    base_load_w: float = 500.0                # house base load, taken off the PV forecast

    # -- power now ------------------------------------------------------------------
    @property
    def export_w(self) -> float:
        return max(0.0, -self.grid_w)

    @property
    def surplus_w(self) -> float:
        """Export plus battery charging: PV nobody in the house uses right now."""
        return self.export_w + max(0.0, -self.battery_w)

    @property
    def deficit_w(self) -> float:
        """Import plus battery discharging."""
        return max(0.0, self.grid_w) + max(0.0, self.battery_w)

    # -- battery and PV today --------------------------------------------------------
    @property
    def battery_need_wh(self) -> float:
        """Energy until the battery is full."""
        return max(0.0, (self.max_soc - self.soc) / 100 * self.battery_capacity_wh)

    @property
    def free_pv_today_wh(self) -> float | None:
        """PV still to come today minus the base load (None without a forecast)."""
        fc = self.forecast
        if fc is None:
            return None
        return max(0.0, fc.remaining_today_wh - self.base_load_w * fc.sun_hours_left_today)

    def battery_fills(self, full_soc: float, safety: float, extra_wh: float = 0.0) -> bool:
        """Will the battery still be full today if `extra_wh` go elsewhere? Full from
        `full_soc`, or the free PV covers the battery's need × `safety` plus `extra_wh`."""
        free = self.free_pv_today_wh
        return self.soc >= full_soc or (free is not None and free >= self.battery_need_wh * safety + extra_wh)

    # -- forecast ------------------------------------------------------------------------
    @property
    def sun_hours_left_today(self) -> float | None:
        return self.forecast.sun_hours_left_today if self.forecast else None

    @property
    def tomorrow_wh(self) -> float | None:
        return self.forecast.tomorrow_wh if self.forecast else None

    @property
    def before_sunrise(self) -> bool:
        """None of today's PV has come yet (needs a forecast; without PV today: before 8 h)."""
        fc = self.forecast
        if fc is not None and fc.today_wh > 0:
            return fc.remaining_today_wh >= 0.98 * fc.today_wh
        return self.now.hour < 8

    @property
    def after_sunset(self) -> bool:
        """Today's PV is as good as over (needs a forecast; without PV today: from 16 h)."""
        fc = self.forecast
        if fc is not None and fc.today_wh > 0:
            return fc.remaining_today_wh <= 0.05 * fc.today_wh
        return self.now.hour >= 16


def from_devices(now: datetime, kostal: dict, isg: dict, isg_web: dict,
                 forecast: ForecastSummary | None, base_load_w: float,
                 room_temp: float | None = None) -> Situation:
    """Build the situation from the latest device values (missing power values count as 0)."""
    kw = isg_web.get("compressor_kw")
    return Situation(
        now=now,
        pv_w=kostal.get("pv_power") or 0.0,
        grid_w=kostal.get("grid_power") or 0.0,
        battery_w=kostal.get("battery_power") or 0.0,
        soc=kostal.get("battery_soc") or 0.0,
        max_soc=kostal.get("battery_max_soc") or 100.0,
        battery_capacity_wh=kostal.get("battery_capacity") or 10240.0,
        outside_temp=isg.get("outside_temp"),
        room_temp=room_temp,                  # the room sensor (the ISG's own value echoes the target)
        dhw_temp=isg.get("dhw_temp"),
        compressor_w=kw * 1000 if kw is not None else None,
        forecast=forecast,
        base_load_w=base_load_w,
    )

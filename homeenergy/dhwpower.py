"""Hot water power limit ("WW-LEISTUNG", 30–100 %) following the PV surplus.

The LWZ has no power setpoint for space heating, but for hot water the ISG web page offers a
power limit (Sommer/Winter, default 80 %). It is not in the Modbus map, so it is written through
the ISG page itself (devices/isg_web.py: POST /save.php, read back afterwards).

While the tank is heated with PV (daily reheat, reserve, anti-legionella):
  still exporting ≥ raise_export_w     → power_high  (take more of the sun per hour)
  drawing ≥ lower_deficit_w            → power_low   (stay inside the surplus)
  in between                           → keep what is set
A bath gets power_high (it is wanted by a time). Otherwise: power_normal, the user's own value.
At most one change per `power_every_min` and `power_max_writes` per day: the value most likely
lands in the controller's settings memory like the setpoints do.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .controller import FAILED, WRITTEN
from .i18n import fnum, t
from .hotwater import DhwConfig, DhwMode

log = logging.getLogger(__name__)
SETTING = "dhw_power"
PV_RUNS = (DhwMode.REHEAT, DhwMode.BOOST, DhwMode.HYGIENE)
RAISE_EXPORT_W = 500.0
LOWER_DEFICIT_W = 300.0


def power_target(cfg: DhwConfig, mode: DhwMode, current: float | None, export_w: float,
                 deficit_w: float) -> tuple[float, str]:
    """The power limit (%) for the hot water mode and the energy flow right now."""
    if mode == DhwMode.BATH:
        return cfg.power_high, t("dhwpower.bad_volle_leistung")
    if mode not in PV_RUNS:
        return cfg.power_normal, t("dhwpower.kein_pv_heizen_des_speichers_normale")
    if export_w >= RAISE_EXPORT_W:
        return cfg.power_high, t("dhwpower.noch_w_einspeisung_mehr_leistung", export_w=fnum(export_w, 0))
    if deficit_w >= LOWER_DEFICIT_W:
        return cfg.power_low, t("dhwpower.w_aus_netz_batterie_weniger_leistung", deficit_w=fnum(deficit_w, 0))
    if current in (cfg.power_low, cfg.power_high, cfg.power_normal):
        return current, t("dhwpower.ueberschuss_passt_leistung_bleibt")
    return cfg.power_normal, t("dhwpower.ueberschuss_passt_normale_leistung")


@dataclass
class PowerState:
    current: float | None = None          # last value written or read back
    last_write_at: datetime | None = None
    writes_day: str = ""
    writes_today: int = 0

    def to_json(self) -> dict:
        return {"current": self.current, "writes_day": self.writes_day, "writes_today": self.writes_today,
                "last_write_at": self.last_write_at.isoformat() if self.last_write_at else None}

    @classmethod
    def from_json(cls, d: dict | None) -> "PowerState":
        d = d or {}
        at = d.get("last_write_at")
        return cls(d.get("current"), datetime.fromisoformat(at) if at else None,
                   d.get("writes_day", ""), d.get("writes_today", 0))


class DhwPowerController:
    def __init__(self, state: PowerState | None = None):
        self.state = state or PowerState()
        self.last: dict | None = None

    def step(self, now: datetime, cfg: DhwConfig, mode: DhwMode, export_w: float, deficit_w: float,
             device_value: float | None, device) -> dict:
        st = self.state
        if device_value is not None:
            st.current = device_value                     # what the ISG says beats what we remember
        target, why = power_target(cfg, mode, st.current, export_w, deficit_w)
        entry = {"time": now.isoformat(timespec="seconds"), "target": target, "current": st.current,
                 "reason": why, "action": "", "outcome": ""}
        day = now.date().isoformat()
        if st.writes_day != day:
            st.writes_day, st.writes_today = day, 0
        if not (cfg.enabled and cfg.apply and cfg.power_control):
            entry["action"] = t("dhwpower.aus") if not cfg.power_control else t("dhwpower.testbetrieb_nicht_geschrieben")
        elif device is None:
            entry["action"] = t("dhwpower.isg_webseite_nicht_angebunden")
        elif st.current is not None and abs(st.current - target) < 0.5:
            entry["action"], entry["outcome"] = t("dhwpower.unveraendert"), "unchanged"
        elif st.writes_today >= cfg.power_max_writes:
            entry["action"] = t("dhwpower.tageslimit_erreicht_aenderungen", power_max_writes=fnum(cfg.power_max_writes, 0))
        elif st.last_write_at and now - st.last_write_at < timedelta(minutes=cfg.power_every_min):
            entry["action"] = t("dhwpower.wartet_hoechstens_alle_min", power_every_min=fnum(cfg.power_every_min, 0))
        else:
            try:
                st.current = float(device.write_setting(SETTING, target))
                st.last_write_at, st.writes_today = now, st.writes_today + 1
                entry["action"], entry["outcome"] = t("dhwpower.written", value=fnum(st.current, 0)), WRITTEN
                log.info(t("dhwpower.log_written", value=fnum(st.current, 0), reason=why), extra={"event": True})
            except Exception as exc:                      # the page is undocumented: never stop the loop
                entry["action"], entry["outcome"] = t("dhwpower.fehler_beim_schreiben", exc=exc), FAILED
                log.warning(t("dhwpower.log_failed", exc=exc))
        self.last = entry
        return entry

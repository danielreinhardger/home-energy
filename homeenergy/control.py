"""One control cycle: build the energy situation once, run heating, then hot water.

Heating goes first: in winter preheating the house is the best use of PV (economics.py);
it only yields to a requested bath. Hot water then knows whether the house is being
preheated right now. Nothing here acts on stale or partial device data.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime
from typing import Callable

from .i18n import t
from .config import AppConfig
from .controller import ControllerState, DhwController, HeatingController, WRITTEN, WriteLimits
from .cop import CopModel
from .dhwpower import DhwPowerController, PowerState
from .forecast import ForecastSummary
from .heating import HeatingInputs, HeatState
from .hotwater import BathRequest, DhwInputs, DhwState, next_occurrence
from .polling import STALE_AFTER_S, Poller
from .situation import Situation, from_devices
from .store import Store

log = logging.getLogger(__name__)


def entry_mode(entry: dict):
    from .hotwater import DhwMode
    try:
        return DhwMode(entry["mode"])
    except ValueError:
        return DhwMode.NORMAL


def heating_limits(cfg: AppConfig) -> WriteLimits:
    h = cfg.heating
    return WriteLimits(h.min_write_interval_min, int(h.max_writes_per_day))


class Control:
    def __init__(self, isg, cfg: Callable[[], AppConfig], store: Store, state: dict, lock: threading.RLock,
                 clock: Callable[[], datetime], on_change: Callable[[], None]):
        self._cfg = cfg
        self._lock = lock
        self._clock = clock
        self._on_change = on_change            # persist state after each cycle and bath change
        c = cfg()
        self.heating = HeatingController(isg, heating_limits(c), lambda e: store.add_decision("heating", e),
                                         ControllerState.from_json(state.get("heating_controller", {}), HeatState))
        self.dhw = DhwController(isg, c.limits, lambda e: store.add_decision("dhw", e),
                                 ControllerState.from_json(state.get("controller", {}), DhwState))
        self.heating.recent.extend(store.recent_decisions("heating", 30))   # newest first, as before
        self.dhw.recent.extend(store.recent_decisions("dhw", 30))
        self.bath: BathRequest | None = BathRequest.from_json(state["bath"]) if state.get("bath") else None
        self.dhw_power = DhwPowerController(PowerState.from_json(state.get("dhw_power")))
        self.skipped = 0
        self.last_situation: Situation | None = None

    def state_json(self) -> dict:
        return {"controller": self.dhw.state.to_json(), "heating_controller": self.heating.state.to_json(),
                "bath": self.bath.to_json() if self.bath else None, "dhw_power": self.dhw_power.state.to_json()}

    def apply_limits(self, cfg: AppConfig) -> None:
        self.dhw.limits, self.heating.limits = cfg.limits, heating_limits(cfg)

    # -- the cycle --------------------------------------------------------------------------
    def run(self, poller: Poller, forecast: ForecastSummary | None, cop: CopModel,
            room_temp: float | None = None) -> bool:
        """One cycle; False (and nothing done) when the device data is missing or stale."""
        if not poller.usable("kostal", "isg"):
            with self._lock:
                self.skipped += 1
            # right after a start the first reads are simply not done yet: no warning for that
            starting = all(poller.status[k].updated is None and not poller.status[k].error for k in ("kostal", "isg"))
            (log.info if starting else log.warning)(
                t("control.regelung_pausiert_daten_von_kostal_oder"), STALE_AFTER_S)
            return False
        cfg, now = self._cfg(), self._clock()
        if self.bath is not None and now >= self.bath.deadline:
            log.info("bath request for %s expired", self.bath.deadline)
            self.bath = None
        isg = poller.latest("isg")
        sit = from_devices(now, poller.latest("kostal"), isg, poller.ok("isg_web"), forecast, cfg.dhw.base_load_w,
                           room_temp)
        self.last_situation = sit

        # 1) space heating
        last_dhw = (self.dhw.last or {}).get("mode")
        h_inp = HeatingInputs(sit, "bath" if self.bath is not None else last_dhw, cop.heating(sit.outside_temp))
        h_entry = self.heating.step(h_inp, cfg.heating, poller.settings("isg").get(HeatingController.SETTING))
        if h_entry["outcome"] == WRITTEN:
            poller.refresh_settings("isg")
        season = any(r["node"] == "h_season" and r["ok"] for r in h_entry["trace"])

        # 2) hot water: daily need with PV, a reserve only before a dull day and only once the house is warm
        d_inp = DhwInputs(sit, bath=self.bath, heating_season=season, heating_boost=h_entry["mode"] == "boost")
        dhw_cfg = replace(cfg.dhw, cop=round(cop.dhw_cop, 2))          # measured hot water COP
        entry = self.dhw.step(d_inp, dhw_cfg, poller.settings("isg").get(DhwController.SETTING))
        if entry["outcome"] == WRITTEN:
            poller.refresh_settings("isg")

        # 3) hot water power limit (ISG web page) following the surplus while PV heats the tank
        web = poller.devices.get("isg_web")
        self.dhw_power.step(now, cfg.dhw, entry_mode(entry), sit.export_w, sit.deficit_w,
                            poller.settings("isg_web").get("dhw_power") if web else None, web)
        self._on_change()
        return True

    # -- bath requests ----------------------------------------------------------------------
    def request_bath(self, hhmm: str | None, temp: float | None) -> BathRequest:
        cfg = self._cfg().dhw
        temp = cfg.bath_temp if temp is None else float(temp)
        if not cfg.min_temp <= temp <= 65:
            raise ValueError(t("control.badtemperatur_c_ausserhalb_65_c", temp=temp, min_temp=cfg.min_temp))
        deadline = next_occurrence(self._clock(), hhmm or cfg.bath_default_time)
        with self._lock:
            self.bath = BathRequest(deadline, temp)
        self._on_change()
        return self.bath

    def cancel_bath(self) -> None:
        with self._lock:
            self.bath = None
        self._on_change()

"""Applies strategy decisions to one heat pump setpoint, safely.

`SetpointController` is generic: it gets a setting key, a pure decide function and
the value to restore. `DhwController` (hot water) and `HeatingController` (room
temperature) are thin configurations of it, so both share the same safety rails.

Safety rails:
- dry run unless `apply` is on;
- targets rounded to the device step and only written when they differ;
- at most one write per `min_write_interval_min` and `max_writes_per_day` per day,
  because the controller stores settings in non-volatile memory;
- when control is switched off *while this instance runs it live*, the comfort
  temperature is written back once. A copy started in test mode never writes,
  even if its saved state says a restore is pending (that copy isn't in charge).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from typing import Any, Callable

from .device import ModbusDevice
from .heating import HeatState, decide_heating
from .hotwater import DhwState, decide
from .rules import Decision, RunState
from .settings import num


log = logging.getLogger(__name__)


@dataclass
class WriteLimits:
    min_write_interval_min: float = num(10.0, 'Min. Abstand zwischen Schreibvorgängen', 'min', 1, 120, 1)
    max_writes_per_day: int = num(24, 'Max. Schreibvorgänge pro Tag', '', 1, 200, 1)


@dataclass
class ControllerState:
    strategy: RunState = field(default_factory=DhwState)     # the strategy's own state (DhwState, HeatState)
    last_write_at: datetime | None = None
    writes_day: str = ""
    writes_today: int = 0
    needs_restore: bool = False          # we changed the target and must restore comfort on disable

    def to_json(self) -> dict:
        return {"strategy": self.strategy.to_json(),
                "last_write_at": self.last_write_at.isoformat() if self.last_write_at else None,
                "writes_day": self.writes_day, "writes_today": self.writes_today,
                "needs_restore": self.needs_restore}

    @classmethod
    def from_json(cls, d: dict, state_cls: type[RunState] = DhwState) -> "ControllerState":
        return cls(state_cls.from_json(d.get("strategy", {})),
                   datetime.fromisoformat(d["last_write_at"]) if d.get("last_write_at") else None,
                   d.get("writes_day", ""), d.get("writes_today", 0), d.get("needs_restore", False))


class SetpointController:
    SETTING = ""                                     # heat pump setting this controller owns
    STATE: type[RunState] = RunState                 # the strategy's state type
    UNIT_LABEL = "°C"

    def __init__(self, heat_pump: ModbusDevice, limits: WriteLimits | None = None,
                 sink: Callable[[dict], None] | None = None, state: ControllerState | None = None):
        self.heat_pump = heat_pump
        self.limits = limits or WriteLimits()
        self.state = state or ControllerState(self.STATE())
        self.sink = sink                                 # where decisions are kept (the store)
        self.recent: deque[dict] = deque(maxlen=200)
        self.last: dict | None = None
        self._lock = threading.Lock()
        self._step = heat_pump.setting(self.SETTING).step
        self._was_live = False      # saw enabled+apply in this process (restore only after that)

    # -- to configure in subclasses -------------------------------------------------
    def decide(self, inp: Any, cfg: Any, state: RunState) -> tuple[Decision, RunState]:
        raise NotImplementedError

    def restore_value(self, cfg: Any) -> float:
        """The user's normal value, written back when live control is switched off."""
        raise NotImplementedError

    # -- generic cycle ------------------------------------------------------------------
    def step(self, inp: Any, cfg: Any, current_target: float | None) -> dict:
        """Run one control cycle. `current_target` is the device's current setpoint."""
        with self._lock:
            decision, self.state.strategy = self.decide(inp, cfg, self.state.strategy)
            target = self._round(decision.target)
            action = self._act(inp.now, cfg, decision, target, current_target)
            entry = {"time": inp.now.isoformat(timespec="seconds"), **decision.to_json(),
                     "target": target, "device_target": current_target, "action": action,
                     "dry_run": not cfg.apply}
            if self.last is None or (entry["mode"], entry["target"], entry["action"]) != (
                    self.last["mode"], self.last["target"], self.last["action"]) or action.startswith("geschrieben"):
                self.recent.appendleft(entry)
                self._append_log(entry)
            self.last = entry
            return entry

    def _act(self, now: datetime, cfg: Any, decision: Decision, target: float,
             current: float | None) -> str:
        if not cfg.enabled or not cfg.apply:
            restore = self._round(self.restore_value(cfg))
            if (self._was_live and self.state.needs_restore and current is not None
                    and abs(current - restore) >= self._step / 2):
                self._was_live = False
                return self._write(now, restore, restore=True)
            self._was_live = False
            self.state.needs_restore = False
            return "Regelung aus" if not cfg.enabled else "Testbetrieb: nicht geschrieben"
        self._was_live = True
        if current is not None and abs(current - target) < self._step / 2:
            return "unverändert"
        limited = self._rate_limited(now)
        if limited:
            return limited
        return self._write(now, target)

    def _rate_limited(self, now: datetime) -> str:
        day = now.date().isoformat()
        if self.state.writes_day != day:
            self.state.writes_day, self.state.writes_today = day, 0
        if self.state.writes_today >= self.limits.max_writes_per_day:
            return f"Tageslimit erreicht ({self.limits.max_writes_per_day} Schreibvorgänge)"
        if self.state.last_write_at and now - self.state.last_write_at < timedelta(
                minutes=self.limits.min_write_interval_min):
            return f"wartet (max. 1 Schreibvorgang je {self.limits.min_write_interval_min:.0f} min)"
        return ""

    def _write(self, now: datetime, value: float, restore: bool = False) -> str:
        try:
            back = self.heat_pump.write_setting(self.SETTING, value)
        except Exception as exc:  # device errors must not kill the loop
            log.warning("writing %s failed: %s", self.SETTING, exc)
            return f"Fehler beim Schreiben: {exc}"
        day = now.date().isoformat()
        if self.state.writes_day != day:
            self.state.writes_day, self.state.writes_today = day, 0
        self.state.writes_today += 1
        self.state.last_write_at = now
        self.state.needs_restore = not restore
        return f"geschrieben: {back:.1f} °C" + (" (Normalwert wiederhergestellt)" if restore else "")

    def _round(self, value: float) -> float:
        return round(round(value / self._step) * self._step, 2)

    def _append_log(self, entry: dict) -> None:
        if not self.sink:
            return
        try:
            self.sink(entry)
        except Exception as exc:                          # never let logging stop the controller
            log.warning("decision log: %s", exc)


class DhwController(SetpointController):
    """PV-led hot water: owns WW Soll Tag."""
    SETTING = "dhw_day"
    STATE = DhwState

    def decide(self, inp, cfg, state):
        return decide(inp, cfg, state)

    def restore_value(self, cfg) -> float:
        return cfg.comfort_temp


class HeatingController(SetpointController):
    """PV-led space heating: owns Raumtemp. Tag (HK1)."""
    SETTING = "room_day_hc1"
    STATE = HeatState

    def decide(self, inp, cfg, state):
        return decide_heating(inp, cfg, state)

    def restore_value(self, cfg) -> float:
        return cfg.normal_temp

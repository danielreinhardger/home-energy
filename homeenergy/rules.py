"""Building blocks shared by the hot water and heating strategies.

- `Decision`: what a strategy decided (mode, target, reasons, metrics, trace).
- `Trace`: the checks made on the way, in order – the explanation tab draws them.
- `RunState`: a strategy's mode plus the two timers every rule here needs:
  "start once a condition has held for N minutes" and "stop once a deficit has held
  for M minutes, but not before the minimum run time".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import ClassVar


class Trace(list):
    """Records each check of one decision: id (a flowchart node), result and the live values."""

    def check(self, node: str, ok: bool | None, detail: str) -> bool | None:
        self.append({"node": node, "ok": ok, "detail": detail})
        return ok


@dataclass
class Decision:
    mode: Enum
    target: float
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)   # the checks made, in order (for the explanation tab)

    def to_json(self) -> dict:
        return {"mode": self.mode.value, "target": self.target, "reasons": self.reasons,
                "metrics": self.metrics, "trace": self.trace}


def held(since: datetime | None, now: datetime, minutes: float) -> bool:
    """True once `since` lies at least `minutes` in the past."""
    return since is not None and now - since >= timedelta(minutes=minutes)


def minutes_since(since: datetime | None, now: datetime) -> float:
    return 0.0 if since is None else (now - since).total_seconds() / 60


@dataclass
class RunState:
    """Mode and timers of one strategy, carried from one decision to the next (and saved)."""
    MODE: ClassVar[type[Enum]]                 # the strategy's mode enum (set by subclasses)

    mode: Enum = None                          # type: ignore[assignment]  (subclasses give the default)
    mode_since: datetime | None = None
    start_since: datetime | None = None        # start condition continuously true since
    stop_since: datetime | None = None         # stop condition continuously true since

    def track_start(self, ok: bool, now: datetime, delay_min: float) -> bool:
        """Count how long `ok` has held; True once it held for `delay_min`."""
        self.start_since = (self.start_since or now) if ok else None
        return ok and held(self.start_since, now, delay_min)

    def track_stop(self, deficit_w: float, limit_w: float, now: datetime, delay_min: float, min_run_min: float) -> bool:
        """True once the deficit exceeded `limit_w` for `delay_min` and the mode ran `min_run_min`."""
        self.stop_since = (self.stop_since or now) if deficit_w > limit_w else None
        return held(self.stop_since, now, delay_min) and held(self.mode_since, now, min_run_min)

    def to_json(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, datetime) else v.value if isinstance(v, Enum) else v)
                for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, d: dict):
        def ts(v):
            return datetime.fromisoformat(v) if v else None
        try:
            mode = cls.MODE(d.get("mode", "normal"))
        except ValueError:                     # a mode this strategy no longer has
            mode = cls.MODE("normal")
        kwargs = {"mode": mode}
        for name, f in cls.__dataclass_fields__.items():
            if name == "mode" or name not in d:
                continue
            kwargs[name] = ts(d[name]) if name.endswith(("_since", "_at")) else d[name]
        return cls(**kwargs)


def de(value: float | None, digits: int = 1) -> str:
    """A number the way the pages show it (German: 1.500 / 46,5), for the explanation texts."""
    if value is None:
        return "–"
    s = f"{value:,.{digits}f}"
    return s.replace(",", "\u2009").replace(".", ",").replace("\u2009", ".")


def stage(id: str, entry: str, title: str, icon: str, intro: str, checks: list[tuple[str, str, str]], *,
          gate: str | None = None, gate_no: str | None = None, all_needed: bool = False,
          fallback: str | None = None) -> dict:
    """One step of a strategy for the explanation tab, in the order the rules check it.

    `entry` is the trace node that means "this step was reached", `gate` a node whose ✓
    makes the step decide, `checks` are (trace node, question, why) – the trace adds
    the live value. Kept next to the rules so a rule change and its text change together.
    """
    s = {"id": id, "entry": entry, "title": title, "icon": icon, "intro": intro,
         "checks": [list(c) for c in checks]}
    if gate:
        s["gate"] = gate
    if gate_no:
        s["gateNo"] = gate_no
    if all_needed:
        s["and"] = True
    if fallback:
        s["fallback"] = fallback
    return s

"""Settings defined once: default, label, unit, range and help text in the dataclass field.

    comfort_temp: float = num(46.0, "Komforttemperatur", "°C", 10, 65, 0.5, "Normales WW Soll Tag.")

The settings forms (config.UI_FIELDS) are generated from these fields, so adding or
removing a setting is one edit. A field the forms must not show is marked `hidden()`;
tests/test_config.py checks that every field is one or the other.
"""
from __future__ import annotations

from dataclasses import MISSING, field, fields
from typing import Any


def num(default: float, label: str, unit: str = "", lo: float | None = None, hi: float | None = None,
        step: float | None = None, help: str = "") -> Any:
    ui: dict = {"type": "number", "label": label, "unit": unit, "help": help}
    if lo is not None:
        ui["min"] = lo
    if hi is not None:
        ui["max"] = hi
    if step is not None:
        ui["step"] = step
    return field(default=default, metadata={"ui": ui})


def flag(default: bool, label: str, help: str = "") -> Any:
    return field(default=default, metadata={"ui": {"type": "bool", "label": label, "help": help}})


def clock(default: str, label: str, help: str = "") -> Any:
    """A time of day, "HH:MM"."""
    return field(default=default, metadata={"ui": {"type": "time", "label": label, "help": help}})


def text(default: str, label: str, help: str = "") -> Any:
    """A line of text (e.g. an address)."""
    return field(default=default, metadata={"ui": {"type": "text", "label": label, "help": help}})


def hidden(default: Any = MISSING, *, default_factory: Any = MISSING, why: str = "") -> Any:
    """A field the settings forms don't show (internal, or edited elsewhere)."""
    return field(default=default, default_factory=default_factory, metadata={"ui": None, "why": why})


def ui_fields(cls) -> dict[str, dict]:
    """Form metadata of a settings dataclass, in field order (hidden fields left out)."""
    return {f.name: dict(f.metadata["ui"]) for f in fields(cls) if f.metadata.get("ui")}


def undeclared(cls) -> list[str]:
    """Fields that are neither a setting nor marked hidden (should be none)."""
    return [f.name for f in fields(cls) if "ui" not in f.metadata]

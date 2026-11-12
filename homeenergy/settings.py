"""Settings defined once: default, unit and range in the dataclass field.

    comfort_temp: float = num(46.0, "°C", 10, 65, 0.5)

The settings forms (config.UI_FIELDS) are generated from these fields, so adding or
removing a setting is one edit here plus its texts in the catalogs: the label under
"cfg.<section>.<field>" and an optional help text under "cfg.<section>.<field>.help"
(homeenergy/lang/*.json, see i18n.py). A field the forms must not show is marked `hidden()`;
tests/test_settings.py checks that every field is one or the other and has its label.
"""
from __future__ import annotations

from dataclasses import MISSING, field, fields
from typing import Any

WORD_UNITS = ("days",)          # units that are words: shown as the text "unit.<code>"


def num(default: float, unit: str = "", lo: float | None = None, hi: float | None = None,
        step: float | None = None) -> Any:
    ui: dict = {"type": "number", "unit": unit}
    if lo is not None:
        ui["min"] = lo
    if hi is not None:
        ui["max"] = hi
    if step is not None:
        ui["step"] = step
    return field(default=default, metadata={"ui": ui})


def flag(default: bool) -> Any:
    return field(default=default, metadata={"ui": {"type": "bool"}})


def clock(default: str) -> Any:
    """A time of day, "HH:MM"."""
    return field(default=default, metadata={"ui": {"type": "time"}})


def text(default: str) -> Any:
    """A line of text (e.g. an address)."""
    return field(default=default, metadata={"ui": {"type": "text"}})


def choice(default: str, options: dict[str, str]) -> Any:
    """One of fixed codes; `options` maps each code to the name the form shows."""
    return field(default=default, metadata={"ui": {"type": "choice", "options": dict(options)}})


def hidden(default: Any = MISSING, *, default_factory: Any = MISSING, why: str = "") -> Any:
    """A field the settings forms don't show (internal, or edited elsewhere)."""
    return field(default=default, default_factory=default_factory, metadata={"ui": None, "why": why})


def ui_fields(cls) -> dict[str, dict]:
    """Form metadata of a settings dataclass, in field order (hidden fields left out)."""
    return {f.name: dict(f.metadata["ui"]) for f in fields(cls) if f.metadata.get("ui")}


def undeclared(cls) -> list[str]:
    """Fields that are neither a setting nor marked hidden (should be none)."""
    return [f.name for f in fields(cls) if "ui" not in f.metadata]

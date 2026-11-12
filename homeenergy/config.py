"""Application configuration: dataclasses, JSON persistence and the settings forms' metadata."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .alerts import AlertConfig
from .controller import WriteLimits
from .forecast import ForecastConfig, PvArray
from .economics import EconomyConfig
from .heating import HeatingConfig
from .hotwater import DhwConfig
from . import i18n
from .settings import choice, ui_fields


@dataclass
class DeviceHosts:
    """The devices' addresses on the LAN – set them in config.json (no sensible default)."""
    isg: str = ""                          # Stiebel Eltron ISG web (Modbus 502, web page)
    kostal: str = ""                       # KOSTAL inverter (Modbus 1502)
    openwb: str = ""                       # empty string = no wallbox


@dataclass
class UiConfig:
    language: str = choice(i18n.DEFAULT, {code: lang["name"] for code, lang in i18n.LANGUAGES.items()})

    def validate(self) -> None:
        if self.language not in i18n.LANGUAGES:
            raise ValueError(i18n.t("cfg.error.language", code=self.language))


@dataclass
class AppConfig:
    devices: DeviceHosts = field(default_factory=DeviceHosts)
    poll_seconds: float = 10.0
    control_seconds: float = 60.0
    web_host: str = "127.0.0.1"
    web_port: int = 8085
    dhw: DhwConfig = field(default_factory=DhwConfig)
    heating: HeatingConfig = field(default_factory=HeatingConfig)
    economy: EconomyConfig = field(default_factory=EconomyConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    limits: WriteLimits = field(default_factory=WriteLimits)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def to_json(self) -> dict:
        return asdict(self)


def _build(cls, data: dict | None):
    """Build a dataclass from a dict, ignoring unknown keys and keeping defaults."""
    obj = cls()
    for f in fields(cls):
        if not data or f.name not in data:
            continue
        value, current = data[f.name], getattr(obj, f.name)
        if f.name == "arrays":
            value = [PvArray(**{k: float(v) for k, v in a.items() if k in ("kwp", "tilt", "azimuth")})
                     for a in value]
        elif hasattr(current, "__dataclass_fields__"):
            value = _build(type(current), value)
        elif isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, (int, float)) and value is not None:
            value = type(current)(value)
        setattr(obj, f.name, value)
    return obj


def load(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    return _build(AppConfig, json.loads(path.read_text(encoding="utf-8")))


def save(cfg: AppConfig, path: Path) -> None:
    write_json_atomic(path, cfg.to_json())


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def update_section(cfg: AppConfig, section: str, patch: dict) -> AppConfig:
    """Return a copy of cfg with one section ('dhw', 'forecast', 'limits') updated and validated."""
    if section not in UI_FIELDS:
        raise KeyError(section)
    current = asdict(getattr(cfg, section))
    unknown = set(patch) - set(current)
    if unknown:
        raise ValueError(i18n.t("cfg.error.unknown", fields=", ".join(sorted(unknown))))
    for name, value in patch.items():
        meta = UI_FIELDS[section].get(name, {})
        if meta.get("type") == "choice" and value not in meta["options"]:
            raise ValueError(i18n.t("cfg.error.choice", label=i18n.t(f"cfg.{section}.{name}"), value=value))
        if meta.get("type") == "text":
            value = "" if value is None else str(value).strip()
        if meta.get("type") == "number" and value is not None:
            value = float(value)
            if "min" in meta and value < meta["min"] or "max" in meta and value > meta["max"]:
                raise ValueError(i18n.t("cfg.error.range", label=i18n.t(f"cfg.{section}.{name}"), value=i18n.fnum(value, 2),
                                        lo=meta.get("min"), hi=meta.get("max")))
        current[name] = value
    new_section = _build(type(getattr(cfg, section)), current)
    if hasattr(new_section, "validate"):
        new_section.validate()
    data = cfg.to_json()
    data[section] = asdict(new_section)
    return _build(AppConfig, data)


# Field metadata for the dashboard's settings forms, generated from the settings dataclasses
# (each field carries its type, unit and range – see settings.py; labels come from the catalogs).
UI_SECTIONS = {"dhw": DhwConfig, "heating": HeatingConfig, "economy": EconomyConfig,
               "forecast": ForecastConfig, "limits": WriteLimits, "alerts": AlertConfig, "ui": UiConfig}
UI_FIELDS: dict[str, dict[str, dict]] = {sec: ui_fields(cls) for sec, cls in UI_SECTIONS.items()}

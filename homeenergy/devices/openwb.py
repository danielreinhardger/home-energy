"""openWB software 2 wallbox (MQTT broker on port 1883, read-only).

openWB publishes every value as a retained MQTT topic. It also publishes the
site topology in `openWB/counter/get/hierarchy`: which meter sits where and
which inverters, batteries and charge points hang below it. On this site:

    counter 0  "Kostal Plenticore Zähler"  (grid connection point)
    ├── inverter 2  "Kostal Plenticore Wechselrichter"
    │   └── bat 4  "Kostal Plenticore Speicher"
    └── cp 6  "openWB2"  (internal charge point, 3 phases)

Sign conventions (openWB): counter + = import; pv − = generation;
bat + = charging; cp + = charging the car.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..mqtt import MqttSubscriber

STALE_SECONDS = 120

CHARGE_MODES = {"pv_charging": "PV-Laden", "instant_charging": "Sofortladen", "scheduled_charging": "Zielladen",
                "eco_charging": "Eco-Laden", "time_charging": "Zeitladen", "standby": "Standby", "stop": "Stop"}

TOPICS = [
    "openWB/counter/get/hierarchy",
    "openWB/counter/set/home_consumption",
    "openWB/counter/set/daily_yield_home_consumption",
    "openWB/counter/+/get/power",
    "openWB/counter/+/get/daily_imported",
    "openWB/counter/+/get/daily_exported",
    "openWB/pv/+/get/power",
    "openWB/pv/+/get/daily_exported",
    "openWB/bat/+/get/power",
    "openWB/bat/+/get/soc",
    "openWB/chargepoint/+/get/#",
    "openWB/chargepoint/+/config",
    "openWB/chargepoint/+/set/charge_template",
    "openWB/system/device/+/component/+/config",
    "openWB/system/version",
]


@dataclass(frozen=True)
class MqttPoint:
    """A value read from one MQTT topic. `{cp}`/`{counter}`/... are filled from the hierarchy."""
    key: str
    label: str
    topic: str
    unit: str = ""
    group: str = ""
    digits: int = 0
    convert: Callable[[Any], Any] | None = None


def _kwh(v):
    return None if v is None else v / 1000


def _bool_de(v):
    return None if v is None else ("ja" if v else "nein")


def _mode(template):
    if not isinstance(template, dict):
        return None
    sel = template.get("chargemode", {}).get("selected")
    return CHARGE_MODES.get(sel, sel)


def _max(values):
    return max(values) if isinstance(values, list) and values else None


def _name(info):
    return info.get("name") if isinstance(info, dict) else None


CP = "openWB/chargepoint/{cp}"
POINTS = (
    MqttPoint("cp_power", "Ladeleistung", f"{CP}/get/power", "W", "Ladepunkt"),
    MqttPoint("cp_plugged", "Fahrzeug angesteckt", f"{CP}/get/plug_state", "", "Ladepunkt", convert=_bool_de),
    MqttPoint("cp_charging", "Lädt", f"{CP}/get/charge_state", "", "Ladepunkt", convert=_bool_de),
    MqttPoint("cp_mode", "Lademodus", f"{CP}/set/charge_template", "", "Ladepunkt", convert=_mode),
    MqttPoint("cp_state", "Status", f"{CP}/get/state_str", "", "Ladepunkt"),
    MqttPoint("cp_vehicle", "Fahrzeug", f"{CP}/get/connected_vehicle/info", "", "Ladepunkt", convert=_name),
    MqttPoint("cp_phases", "Phasen in Benutzung", f"{CP}/get/phases_in_use", "", "Ladepunkt"),
    MqttPoint("cp_current", "Ladestrom (max. Phase)", f"{CP}/get/currents", "A", "Ladepunkt", 1, _max),
    MqttPoint("cp_daily", "Geladen heute", f"{CP}/get/daily_imported", "kWh", "Energie", 2, _kwh),
    MqttPoint("cp_total", "Geladen gesamt", f"{CP}/get/imported", "kWh", "Energie", 0, _kwh),
    MqttPoint("grid_power", "Netz (+Bezug/−Einspeisung)", "openWB/counter/{counter}/get/power", "W", "Netz"),
    MqttPoint("grid_import_day", "Netzbezug heute", "openWB/counter/{counter}/get/daily_imported", "kWh", "Energie", 2, _kwh),
    MqttPoint("grid_export_day", "Einspeisung heute", "openWB/counter/{counter}/get/daily_exported", "kWh", "Energie", 2, _kwh),
    MqttPoint("pv_power", "PV (−Erzeugung)", "openWB/pv/{inverter}/get/power", "W", "PV"),
    MqttPoint("pv_day", "PV heute", "openWB/pv/{inverter}/get/daily_exported", "kWh", "Energie", 2, _kwh),
    MqttPoint("bat_power", "Batterie (+Laden/−Entladen)", "openWB/bat/{bat}/get/power", "W", "Batterie"),
    MqttPoint("bat_soc", "Batterie SOC", "openWB/bat/{bat}/get/soc", "%", "Batterie"),
    MqttPoint("home_power", "Hausverbrauch (ohne Ladepunkt)", "openWB/counter/set/home_consumption", "W", "Haus"),
    MqttPoint("home_day", "Hausverbrauch heute", "openWB/counter/set/daily_yield_home_consumption", "kWh", "Energie", 2, _kwh),
    MqttPoint("version", "openWB-Version", "openWB/system/version", "", "Info"),
)


@dataclass(frozen=True)
class OpenWbSpec:
    key: str = "openwb"
    name: str = "openWB Wallbox"
    points: tuple[MqttPoint, ...] = POINTS
    settings: tuple = ()
    page_only: tuple[str, ...] = ("Lademodus, Ladeprofile und alle Einstellungen: openWB-Weboberfläche",)


class OpenWbDevice:
    """Read-only openWB access with the same surface as ModbusDevice."""

    def __init__(self, host: str, subscriber: MqttSubscriber | None = None):
        self.spec = OpenWbSpec()
        self.host = host
        self.mqtt = subscriber or MqttSubscriber(host, TOPICS)
        self.mqtt.start()

    def _json(self, topic: str) -> Any:
        raw = self.mqtt.get(topic)
        if raw is None or raw == "":
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def hierarchy(self) -> list | None:
        h = self._json("openWB/counter/get/hierarchy")
        return h if isinstance(h, list) else None

    def component_names(self) -> dict[int, str]:
        names = {}
        for topic, raw in self.mqtt.snapshot().items():
            if topic.startswith("openWB/system/device/") and "/component/" in topic and topic.endswith("/config"):
                try:
                    cfg = json.loads(raw)
                    names[int(cfg["id"])] = cfg.get("name") or ""
                except (ValueError, KeyError, TypeError):
                    continue
            elif topic.startswith("openWB/chargepoint/") and topic.endswith("/config") and topic.count("/") == 3:
                try:
                    cfg = json.loads(raw)
                    names[int(cfg["id"])] = cfg.get("name") or ""
                except (ValueError, KeyError, TypeError):
                    continue
        return names

    def ids(self) -> dict[str, int]:
        """First component id per type, found by walking the hierarchy."""
        found: dict[str, int] = {}

        def walk(nodes):
            for n in nodes or []:
                kind = {"cp": "cp", "bat": "bat", "inverter": "inverter", "counter": "counter"}.get(n.get("type"))
                if kind and kind not in found:
                    found[kind] = n["id"]
                walk(n.get("children"))
        walk(self.hierarchy())
        return found

    def component_powers(self) -> dict[int, float | None]:
        """Live power of every component in the hierarchy, by openWB id (openWB signs)."""
        prefix = {"counter": "counter", "inverter": "pv", "bat": "bat", "cp": "chargepoint"}
        out: dict[int, float | None] = {}

        def walk(nodes):
            for n in nodes or []:
                p = prefix.get(n.get("type"))
                if p:
                    v = self._json(f"openWB/{p}/{n['id']}/get/power")
                    out[n["id"]] = float(v) if isinstance(v, (int, float)) else None
                walk(n.get("children"))
        walk(self.hierarchy())
        return out

    def read_values(self) -> dict[str, Any]:
        if not self.mqtt.connected:
            raise ConnectionError(f"MQTT {self.host}: {self.mqtt.last_error or 'nicht verbunden'}")
        if self.mqtt.last_message is None or time.time() - self.mqtt.last_message > STALE_SECONDS:
            raise ConnectionError(f"MQTT {self.host}: keine aktuellen Daten")
        ids = self.ids()
        out = {}
        for p in self.spec.points:
            try:
                topic = p.topic.format(**ids)
            except KeyError:          # component type not present on this site
                out[p.key] = None
                continue
            v = self._json(topic)
            out[p.key] = p.convert(v) if p.convert else v
        return out

    def read_settings(self) -> dict:
        return {}

    def write_setting(self, key: str, value: float):
        raise PermissionError("openWB ist nur lesend angebunden")

    def close(self) -> None:
        self.mqtt.stop()

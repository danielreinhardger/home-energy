"""The real room temperature, pushed by a sensor (the heat pump has no room sensor of its own).

Without a Stiebel room control unit the LWZ reports its room *target* as "Raum-Ist HK1", so the
heating strategy can't use it. Any sensor that can call a URL sends its readings here instead, e.g.
a Shelly H&T Gen3 with a webhook on temperature.change and humidity.change:

    http://<nas>:8085/api/sensor/room?t=${status["temperature:0"].tC}&h=${status["humidity:0"].rh}

The last reading is kept in state.json (a battery sensor may report only every so often);
`current()` returns it while it is younger than the configured maximum age.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

TEMP_RANGE = (5.0, 35.0)            # a living room, not a sensor in the freezer or the sun


@dataclass(frozen=True)
class Reading:
    temp: float
    humidity: float | None
    at: datetime


class RoomSensor:
    def __init__(self, state: dict | None = None):
        self.last: Reading | None = None
        self.count = 0
        s = state or {}
        if s.get("temp") is not None and s.get("at"):
            try:
                self.last = Reading(float(s["temp"]), s.get("humidity"), datetime.fromisoformat(s["at"]))
            except (TypeError, ValueError):
                self.last = None

    def to_json(self) -> dict | None:
        if self.last is None:
            return None
        return {"temp": self.last.temp, "humidity": self.last.humidity,
                "at": self.last.at.isoformat(timespec="seconds")}

    def update(self, now: datetime, temp, humidity=None) -> Reading:
        """Store a reading (strings as they come from a URL; '21,5' works too). Raises ValueError."""
        t = _number(temp, "t")
        if t is None or not TEMP_RANGE[0] <= t <= TEMP_RANGE[1]:
            raise ValueError(f"Raumtemperatur fehlt oder liegt außerhalb {TEMP_RANGE[0]:.0f}…{TEMP_RANGE[1]:.0f} °C")
        h = _number(humidity, "h")
        if h is not None and not 0 <= h <= 100:
            h = None
        self.last = Reading(round(t, 2), None if h is None else round(h, 1), now)
        self.count += 1
        return self.last

    def current(self, now: datetime, max_age_min: float) -> Reading | None:
        if self.last is None or now - self.last.at > timedelta(minutes=max_age_min):
            return None
        return self.last

    def view(self, now: datetime, max_age_min: float) -> dict:
        r = self.last
        return {"temp": r.temp if r else None, "humidity": r.humidity if r else None,
                "at": r.at.isoformat(timespec="minutes") if r else None,
                "age_min": None if r is None else round((now - r.at).total_seconds() / 60),
                "fresh": self.current(now, max_age_min) is not None}


def _number(value, name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"{name}: Zahl erwartet, bekommen {str(value)[:20]!r}") from None

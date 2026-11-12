"""PV forecast in the service: periodic fetch, calibration against the real yield.

forecast.py holds the pure parts (fetching, summarising, the calibration maths); this
class keeps the latest forecast and weather, refreshes them in the background and logs,
per local day, what was forecast the day before (`ahead_wh`, the last fetch before the day
began), on the day itself (`same_wh`) and what the inverter really yielded (`actual_wh`).
The calibration is refitted from that log after each day; the log is also what the
dashboard shows as forecast accuracy.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

from .forecast import (Calibration, ForecastSummary, HourlyPv, Weather, fetch_hourly, fetch_weather, outlook,
                       local_day_totals, summarize)

LOG_DAYS = 90                                   # days kept in the forecast log

log = logging.getLogger(__name__)


class Forecaster:
    def __init__(self, cfg: Callable, clock: Callable[[], datetime], lock: threading.RLock,
                 state: dict, on_change: Callable[[], None]):
        self._cfg = cfg                         # () -> ForecastConfig (follows config changes)
        self._clock = clock
        self._lock = lock
        self._on_change = on_change             # persist state (calibration, raw forecasts)
        self.calibration = Calibration.from_json(state.get("calibration", {}))
        self.log: dict[str, dict] = state.get("forecast_log", {})
        self.hours: list[HourlyPv] = []
        self.weather: Weather | None = None
        self.fetched: datetime | None = None
        self.error: str | None = None
        self.last_yield: tuple[str, float] | None = None

    def state_json(self) -> dict:
        return {"calibration": {"factor": self.calibration.factor, "days": self.calibration.days},
                "forecast_log": self.log}

    def loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            fc = self._cfg()
            due = self.fetched is None or self._clock() - self.fetched >= timedelta(minutes=fc.refresh_minutes)
            if fc.usable and due:
                self.refresh()
            stop.wait(60)

    def refresh(self) -> None:
        try:
            hours = fetch_hourly(self._cfg())
        except Exception as exc:
            with self._lock:
                self.error = str(exc)
            log.warning("forecast: %s", exc)
            return
        try:
            weather = fetch_weather(self._cfg())
        except Exception as exc:   # icons are optional; keep the PV forecast
            log.warning("weather: %s", exc)
            weather = None
        with self._lock:
            self.hours, self.fetched, self.error = hours, self._clock(), None
            self.weather = weather
            self._log_forecast(hours)
        self._on_change()

    def _log_forecast(self, hours: list[HourlyPv]) -> None:
        """Remember the (uncalibrated) forecast per complete local day; past days stay untouched."""
        now = self._clock()
        for day, (wh, n) in local_day_totals(hours, now.tzinfo).items():
            if n < 23 or day < now.date():               # incomplete (23 h: DST day)
                continue
            entry = self.log.setdefault(day.isoformat(), {})
            entry["ahead_wh" if day > now.date() else "same_wh"] = round(wh, 1)
        for old in sorted(self.log)[:-LOG_DAYS]:
            del self.log[old]

    def accuracy(self, days: int = 14) -> dict:
        """The calibration and the newest finished days: forecast the day before, on the day, real."""
        kwh = lambda v: None if v is None else round(v / 1000, 2)
        rows = [{"day": d, "ahead_kwh": kwh(e.get("ahead_wh")), "same_kwh": kwh(e.get("same_wh")),
                 "actual_kwh": kwh(e.get("actual_wh"))}
                for d, e in sorted(self.log.items()) if e.get("actual_wh") is not None][-days:]
        return {"factor": round(self.calibration.factor, 3), "days": self.calibration.days,
                "min_days": self.calibration.min_days, "recent": rows[::-1]}

    def reset(self) -> None:
        """Forget the forecast (the location or the panels changed)."""
        self.hours, self.fetched = [], None

    def summary(self) -> ForecastSummary | None:
        with self._lock:
            if not self._cfg().usable or not self.hours:
                return None
            return summarize(self.hours, self._clock(), self.calibration.factor, self.fetched, weather=self.weather)

    def outlook(self) -> dict:
        """The 3-day outlook for the page (fetched on demand, not with every state)."""
        with self._lock:
            if not self._cfg().usable or not self.hours:
                return {"days": [], "error": self.error or "Prognose noch nicht geladen"}
            return {"days": outlook(self.hours, self.weather, self._clock(), self.calibration.factor),
                    "fetched_at": self.fetched.isoformat() if self.fetched else None,
                    "calibration": round(self.calibration.factor, 3), "weather": self.weather is not None}

    def track_yield(self, yield_kwh: float | None) -> None:
        """At day change, compare yesterday's yield with yesterday's forecast."""
        if yield_kwh is None:
            return
        today = self._clock().date().isoformat()
        prev = self.last_yield
        if prev and prev[0] != today:
            with self._lock:
                self.log.setdefault(prev[0], {})["actual_wh"] = round(prev[1] * 1000, 1)
                if self.calibration.refit(self.log):
                    log.info("forecast calibration now %.3f from %d days", self.calibration.factor,
                             self.calibration.days)
            self._on_change()
        self.last_yield = (today, yield_kwh)

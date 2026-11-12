"""Device reading: one thread per device, latest values, freshness and read timings.

Each device is read by its own thread, so a slow or hanging device never delays the
others or the control loop. The rest of the service only reads the latest snapshot.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .i18n import t
from .registers import device_name

log = logging.getLogger(__name__)

# Seconds between reads (others: poll_seconds); the ISG web pages are slow and only change once a minute.
DEVICE_INTERVAL_S = {"isg_web": 60.0}
STALE_AFTER_S = 90.0               # control needs Kostal and ISG data younger than this
SETTINGS_EVERY_S = 60.0            # settings change rarely: re-read them once a minute


@dataclass
class DeviceStatus:
    values: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    error: str | None = None
    updated: float | None = None
    read_ms: float | None = None        # duration of the last read
    read_ms_avg: float | None = None    # moving average
    read_ms_max: float = 0.0
    reads: int = 0
    failures: int = 0


class Poller:
    def __init__(self, devices: dict, lock: threading.RLock, poll_seconds: Callable[[], float]):
        self.devices = devices
        self.status = {k: DeviceStatus() for k in devices}
        self._lock = lock
        self._poll_seconds = poll_seconds
        self._last_settings: dict[str, float] = {}

    def interval(self, key: str) -> float:
        return DEVICE_INTERVAL_S.get(key, self._poll_seconds())

    def loop(self, key: str, stop: threading.Event) -> None:
        """Read one device on its own schedule until `stop` is set."""
        while not stop.is_set():
            started = time.monotonic()
            self.read(key)
            stop.wait(max(1.0, self.interval(key) - (time.monotonic() - started)))

    def read(self, key: str) -> None:
        dev, st = self.devices[key], self.status[key]
        started = time.monotonic()
        try:
            values = dev.read_values()
            due = time.monotonic() - self._last_settings.get(key, -1e9) >= SETTINGS_EVERY_S
            settings = st.settings
            if (dev.spec.settings or getattr(dev.spec, "reads_settings", False)) and (due or st.settings is None):
                settings = dev.read_settings()
                self._last_settings[key] = time.monotonic()
            with self._lock:
                was_error = st.error
                st.values, st.settings, st.error, st.updated = values, settings, None, time.time()
            if was_error:
                log.info(t("poll.answers_again", device=device_name(key)), extra={"event": True})
        except Exception as exc:
            with self._lock:
                st.error, st.failures = str(exc), st.failures + 1
            log.warning("%s: %s", key, exc)
        ms = (time.monotonic() - started) * 1000
        with self._lock:
            st.reads += 1
            st.read_ms, st.read_ms_max = ms, max(st.read_ms_max, ms)
            st.read_ms_avg = ms if st.read_ms_avg is None else 0.9 * st.read_ms_avg + 0.1 * ms

    def read_all(self) -> None:
        for key in self.devices:
            self.read(key)

    def refresh_settings(self, key: str) -> None:
        """Re-read a device's settings now (after a write)."""
        try:
            settings = self.devices[key].read_settings()
            with self._lock:
                self.status[key].settings = settings
        except Exception as exc:
            log.warning("settings refresh %s: %s", key, exc)

    # -- reading the snapshot ------------------------------------------------------------
    def fresh(self, key: str) -> bool:
        st = self.status.get(key)
        return bool(st and st.updated and time.time() - st.updated <= STALE_AFTER_S)

    def latest(self, key: str) -> dict:
        """The last values read, even if the latest read failed."""
        st = self.status.get(key)
        return (st.values or {}) if st else {}

    def ok(self, key: str) -> dict:
        """The last values, but empty while the device reports an error (optional devices)."""
        st = self.status.get(key)
        return (st.values or {}) if st and not st.error else {}

    def settings(self, key: str) -> dict:
        st = self.status.get(key)
        return (st.settings or {}) if st else {}

    def usable(self, *keys: str) -> bool:
        """All these devices answered recently and without error – safe to act on."""
        return all(self.status[k].values and not self.status[k].error and self.fresh(k) for k in keys)

    def perf(self) -> dict:
        def r(v):
            return None if v is None else round(v, 1)
        with self._lock:
            return {k: {"interval_s": self.interval(k), "read_ms": r(st.read_ms), "read_ms_avg": r(st.read_ms_avg),
                        "read_ms_max": r(st.read_ms_max), "reads": st.reads, "failures": st.failures,
                        "age_s": r(time.time() - st.updated) if st.updated else None}
                    for k, st in self.status.items()}

"""Bookkeeping in the service: the daily ledger, the minute history and database upkeep."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .finance import Ledger
from .store import Store, to_ts

log = logging.getLogger(__name__)

BACKUP_HOUR = 3                    # nightly upkeep: prune, vacuum, backup copy per weekday
ROLLUP_EVERY_S = 900               # 15-minute averages
SAVE_LEDGER_EVERY_S = 300


class Bookkeeper:
    def __init__(self, store: Store, ledger: Ledger, backup_dir: Path, clock: Callable[[], datetime],
                 lock: threading.RLock):
        self.store, self.ledger = store, ledger
        self._backup_dir = backup_dir
        self._clock = clock
        self._lock = lock
        self._ledger_saved = 0.0
        self._last_rollup = 0.0
        self._upkeep_day: str | None = None

    def track_finance(self, totals: dict, outside_temp: float | None, price_buy: float, price_feed: float) -> None:
        """Keep today's running day totals in the ledger (saved every 5 min and on day change)."""
        now = self._clock()
        with self._lock:
            new_day = now.date().isoformat() not in self.ledger.days
            self.ledger.update(now.date(), totals, price_buy, price_feed,
                               allow_decrease=now.hour == 0 and now.minute < 5, hour=now.hour)
            self.ledger.add_outside(now.date(), outside_temp)
        if new_day or time.monotonic() - self._ledger_saved > SAVE_LEDGER_EVERY_S:
            self._ledger_saved = time.monotonic()
            with self._lock:
                self.ledger.save()

    def record_history(self, sample: dict) -> None:
        ts = to_ts(self._clock()) // 60 * 60                # one row per minute
        try:
            self.store.add_sample(ts, sample)
        except Exception as exc:                            # sqlite3.Error: never stop the control loop
            log.warning("history: %s", exc)

    def upkeep(self, now_mono: float) -> None:
        """15-minute averages every 15 min; at night prune old rows and write a backup copy."""
        try:
            if now_mono - self._last_rollup >= ROLLUP_EVERY_S:
                self._last_rollup = now_mono
                self.store.rollup()
            now = self._clock()
            if now.hour == BACKUP_HOUR and self._upkeep_day != now.date().isoformat():
                self._upkeep_day = now.date().isoformat()
                removed = self.store.prune()
                self.store.backup(self._backup_dir / f"home-energy-{now.strftime('%a').lower()}.sqlite")
                log.info("database upkeep: removed %s, backup written", removed)
        except Exception as exc:
            log.warning("database upkeep: %s", exc)

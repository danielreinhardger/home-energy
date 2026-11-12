"""SQLite store: measurement history, daily ledger and control decisions in one file.

Tables
  samples       one row per minute, one REAL column per value          kept RAW_DAYS
  samples_15m   15-minute averages (+ min/max of temperatures)         kept ROLLUP_DAYS
  days          daily ledger rows (finance.DayRecord)                  kept forever
  decisions     control decisions (hot water, heating), JSON entry     kept DECISION_DAYS
  meta          key/value (schema version via PRAGMA user_version)

Times are Unix seconds (UTC): they sort, compare and survive DST changes. A new value in
a sample becomes a new column automatically. One connection behind a lock: the service is
the only writer and the volume is tiny, so this is simpler than a connection pool.
WAL keeps the file consistent after a power cut; `backup()` writes a safe copy (never copy
the live file while it is being written).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

RAW_DAYS = 90
ROLLUP_DAYS = 5 * 366
DECISION_DAYS = 90
EVENT_DAYS = 90
EVENT_MERGE_S = 600       # the same message again within 10 min counts up instead of adding a row
ROLLUP_S = 15 * 60
MINMAX = ("outside_temp", "room_sensor", "dhw_temp")      # temperatures also keep min/max per 15 min
# Values no longer recorded: their columns are dropped (schema 3) and never created again.
# room_temp held the ISG's echo of its own room target until 2026-11-11 – no measurement.
RETIRED = ("room_temp",)
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
SCHEMA_VERSION = 3


def to_ts(t: datetime | str | int | float) -> int:
    if isinstance(t, (int, float)):
        return int(t)
    if isinstance(t, str):
        t = datetime.fromisoformat(t.replace(" ", "+"))
    if t.tzinfo is None:
        t = t.astimezone()
    return int(t.timestamp())


def _q(cols: Iterable[str]) -> str:
    """Quoted, comma-separated column names (names are checked against _NAME first)."""
    return ", ".join('"' + c + '"' for c in cols)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA auto_vacuum = INCREMENTAL")     # only takes effect on a new file
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = NORMAL")
            self._migrate()
            self._columns = self._table_columns("samples")

    # -- schema -------------------------------------------------------------------
    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._db.executescript("""
                BEGIN;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS samples_15m (ts INTEGER PRIMARY KEY, n INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL,
                    mode TEXT, target REAL, action TEXT, entry TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS decisions_kind_ts ON decisions (kind, ts);
                PRAGMA user_version = 1;
                COMMIT;
            """)
        if version < 2:
            self._db.executescript("""
                BEGIN;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, last_ts INTEGER NOT NULL,
                    level TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS events_last_ts ON events (last_ts);
                PRAGMA user_version = 2;
                COMMIT;
            """)
        if version < 3:
            self._drop_retired()

    def _drop_retired(self) -> None:
        """Drop the columns of RETIRED values (with their 15-minute min/max) in one transaction."""
        with self._transaction():
            for table in ("samples", "samples_15m"):
                have = set(self._table_columns(table))
                for name in RETIRED:
                    for col in (name, f"{name}_min", f"{name}_max"):
                        if col in have:
                            self._db.execute(f'ALTER TABLE {table} DROP COLUMN "{col}"')
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Run the block as one transaction under the lock: committed, or rolled back on any error."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                # the rollback also undoes columns added in the block: re-read the cache
                self._columns = self._table_columns("samples")
                raise
            self._db.execute("COMMIT")

    def _table_columns(self, table: str) -> list[str]:
        return [r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")]

    def _ensure_column(self, name: str) -> None:
        if name in self._columns:
            return
        if not _NAME.match(name):
            raise ValueError(f"invalid column name: {name!r}")
        self._db.execute(f'ALTER TABLE samples ADD COLUMN "{name}" REAL')
        rollup = set(self._table_columns("samples_15m"))
        extra = [name] + ([f"{name}_min", f"{name}_max"] if name in MINMAX else [])
        for col in extra:
            if col not in rollup:
                self._db.execute(f'ALTER TABLE samples_15m ADD COLUMN "{col}" REAL')
        self._columns.append(name)

    @property
    def value_columns(self) -> list[str]:
        return [c for c in self._columns if c != "ts"]

    # -- meta ---------------------------------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # -- samples ------------------------------------------------------------------------
    def add_samples(self, rows: Iterable[tuple[int, dict]]) -> int:
        """Insert (ts, {name: number}) rows; a row for an existing minute replaces it."""
        n = 0
        with self._transaction():
            for ts, values in rows:
                values = {k: v for k, v in values.items() if k != "t" and k not in RETIRED}
                nums = {k: float(v) for k, v in values.items()
                        if isinstance(v, (int, float)) and not isinstance(v, bool)}
                nulls = [k for k, v in values.items() if v is None]
                for k in list(nums) + nulls:
                    self._ensure_column(k)
                cols = list(nums) + nulls
                q = (f"INSERT OR REPLACE INTO samples (ts{', ' + _q(cols) if cols else ''}) "
                     f"VALUES ({', '.join('?' * (len(cols) + 1))})")
                self._db.execute(q, [int(ts)] + [nums.get(c) for c in cols])
                n += 1
        return n

    def add_sample(self, ts: int, values: dict) -> None:
        self.add_samples([(ts, values)])

    def pairs(self, x: str, y: str, start: int) -> list[tuple[float, float]]:
        """(x, y) of every minute since `start` where both are set (e.g. outside temp vs. live COP)."""
        if x not in self._columns or y not in self._columns:
            return []
        with self._lock:
            rows = self._db.execute(f'SELECT "{x}", "{y}" FROM samples WHERE ts > ? AND "{x}" IS NOT NULL '
                                    f'AND "{y}" IS NOT NULL', (start,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def samples(self, start: int, end: int | None = None, columns: bool = False) -> list[dict] | dict:
        """Raw minute samples with start < ts ≤ end, as dicts with an ISO time `t`
        (or, with `columns`, one list per value – see _as_columns)."""
        cols = self.value_columns
        q = f"SELECT ts{', ' + _q(cols) if cols else ''} FROM samples WHERE ts > ?"
        args: list[Any] = [start]
        if end is not None:
            q += " AND ts <= ?"
            args.append(end)
        with self._lock:
            rows = self._db.execute(q + " ORDER BY ts", args).fetchall()
        return self._as_columns(rows, cols) if columns else [self._row(r, cols) for r in rows]

    def series(self, start: int, end: int, points: int = 800, columns: bool = False) -> dict:
        """Samples between start and end, at most ~`points` rows: minute data for short spans,
        the 15-minute table beyond RAW_DAYS or for long spans, averaged into equal buckets."""
        span = max(1, end - start)
        bucket = max(60, -(-span // max(10, points)))           # ceil division
        raw_from = time.time() - RAW_DAYS * 86400
        use_rollup = bucket >= ROLLUP_S or start < raw_from
        table, step = ("samples_15m", ROLLUP_S) if use_rollup else ("samples", 60)
        bucket = -(-bucket // step) * step
        cols = self.value_columns
        sel = ", ".join(f'avg("{c}") AS "{c}"' for c in cols)
        q = (f"SELECT (ts / {bucket}) * {bucket} AS ts{', ' + sel if sel else ''} FROM {table} "
             f"WHERE ts >= ? AND ts <= ? GROUP BY ts / {bucket} ORDER BY ts")
        with self._lock:
            rows = self._db.execute(q, (start, end)).fetchall()
        return {"resolution_s": bucket, "source": "15min" if use_rollup else "minute",
                "samples": self._as_columns(rows, cols) if columns else [self._row(r, cols) for r in rows]}

    @staticmethod
    def _as_columns(rows: list[sqlite3.Row], cols: list[str]) -> dict:
        """{"t": [unix seconds…], "pv_w": [values…], …}: each name once instead of once per row,
        watts as whole numbers, other values to 0.1; columns without any value are left out.
        About a quarter of the row format's size for the 48 h of minute data."""
        out: dict[str, list] = {"t": [r["ts"] for r in rows]}
        for c in cols:
            vals = [r[c] for r in rows]
            if all(v is None for v in vals):
                continue
            digits = 0 if c.endswith("_w") else 1
            out[c] = [None if v is None else (round(v) if digits == 0 else round(v, 1)) for v in vals]
        return out

    @staticmethod
    def _row(r: sqlite3.Row, cols: list[str]) -> dict:
        d = {"t": iso(r["ts"])}
        for c in cols:
            v = r[c]
            d[c] = round(v, 1) if isinstance(v, float) else v
        return d

    # -- rollup and upkeep -------------------------------------------------------------
    def rollup(self, now: float | None = None) -> int:
        """Average every finished 15-minute bucket that is not rolled up yet."""
        now = int(now if now is not None else time.time())
        done_until = self.get_meta("rollup_until", 0)
        until = (now // ROLLUP_S) * ROLLUP_S                    # only finished buckets
        if until <= done_until:
            return 0
        cols = self.value_columns
        if not cols:
            return 0
        exprs = [f'avg("{c}")' for c in cols]
        names = list(cols)
        for c in MINMAX:
            if c in cols:
                exprs += [f'min("{c}")', f'max("{c}")']
                names += [f"{c}_min", f"{c}_max"]
        with self._lock:
            have = set(self._table_columns("samples_15m"))
            for c in names:
                if c not in have:
                    self._db.execute(f'ALTER TABLE samples_15m ADD COLUMN "{c}" REAL')
            cur = self._db.execute(
                f"INSERT OR REPLACE INTO samples_15m (ts, n, {_q(names)}) "
                f"SELECT (ts / {ROLLUP_S}) * {ROLLUP_S}, count(*), {', '.join(exprs)} FROM samples "
                f"WHERE ts >= ? AND ts < ? GROUP BY ts / {ROLLUP_S}",
                ((done_until // ROLLUP_S) * ROLLUP_S, until))
        self.set_meta("rollup_until", until)
        return cur.rowcount

    def prune(self, now: float | None = None) -> dict:
        now = int(now if now is not None else time.time())
        rolled = self.get_meta("rollup_until", 0)
        with self._lock:
            raw = self._db.execute("DELETE FROM samples WHERE ts < ? AND ts < ?",
                                   (now - RAW_DAYS * 86400, rolled)).rowcount   # never drop un-rolled minutes
            agg = self._db.execute("DELETE FROM samples_15m WHERE ts < ?", (now - ROLLUP_DAYS * 86400,)).rowcount
            dec = self._db.execute("DELETE FROM decisions WHERE ts < ?", (now - DECISION_DAYS * 86400,)).rowcount
            ev = self._db.execute("DELETE FROM events WHERE last_ts < ?", (now - EVENT_DAYS * 86400,)).rowcount
            self._db.execute("PRAGMA incremental_vacuum")
        return {"samples": raw, "samples_15m": agg, "decisions": dec, "events": ev}

    def backup(self, target: Path) -> None:
        """Consistent copy of the live database (SQLite online backup)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        dst = sqlite3.connect(str(tmp))
        try:
            with self._lock:
                self._db.backup(dst)
        finally:
            dst.close()
        tmp.replace(target)

    # -- days (finance ledger) ---------------------------------------------------------
    def load_days(self) -> dict[str, dict]:
        with self._lock:
            return {r["day"]: json.loads(r["data"]) for r in self._db.execute("SELECT day, data FROM days")}

    def save_days(self, days: dict[str, dict]) -> None:
        with self._transaction():
            self._db.executemany("INSERT OR REPLACE INTO days (day, data) VALUES (?, ?)",
                                 [(k, json.dumps(v)) for k, v in days.items()])

    # -- decisions -------------------------------------------------------------------
    def add_decision(self, kind: str, entry: dict) -> None:
        self.add_decisions(kind, [entry])

    def add_decisions(self, kind: str, entries: Iterable[dict]) -> None:
        rows = []
        for e in entries:
            try:
                ts = to_ts(e["time"])
            except (KeyError, ValueError, TypeError):
                continue
            target = e.get("target")
            rows.append((ts, kind, e.get("mode"), target if isinstance(target, (int, float)) else None,
                         e.get("action"), json.dumps(e, ensure_ascii=False, default=str)))
        with self._lock:
            self._db.executemany("INSERT INTO decisions (ts, kind, mode, target, action, entry) VALUES (?, ?, ?, ?, ?, ?)",
                                 rows)

    def recent_decisions(self, kind: str, limit: int = 30) -> list[dict]:
        """Newest first, like the controllers' `recent` lists."""
        with self._lock:
            rows = self._db.execute("SELECT entry FROM decisions WHERE kind = ? ORDER BY ts DESC, id DESC LIMIT ?",
                                    (kind, limit)).fetchall()
        return [json.loads(r["entry"]) for r in rows]

    # -- events (error log) ------------------------------------------------------------
    def add_event(self, ts: float, level: str, source: str, message: str) -> None:
        """Log one event; a repeat of the latest same message within EVENT_MERGE_S only counts up."""
        ts = int(ts)
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM events WHERE source = ? AND message = ? AND level = ? AND last_ts >= ? "
                "ORDER BY last_ts DESC LIMIT 1", (source, message, level, ts - EVENT_MERGE_S)).fetchone()
            if row:
                self._db.execute("UPDATE events SET last_ts = ?, count = count + 1 WHERE id = ?", (ts, row["id"]))
            else:
                self._db.execute("INSERT INTO events (ts, last_ts, level, source, message) VALUES (?, ?, ?, ?, ?)",
                                 (ts, ts, level, source, message))

    def events(self, limit: int = 300, levels: tuple[str, ...] | None = None) -> list[dict]:
        """Newest first (by the last occurrence)."""
        sql, args = "SELECT * FROM events", []
        if levels:
            sql += f" WHERE level IN ({', '.join('?' * len(levels))})"
            args += list(levels)
        sql += " ORDER BY last_ts DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, limit)).fetchall()
        return [{"first": iso(r["ts"]), "last": iso(r["last_ts"]), "level": r["level"], "source": r["source"],
                 "message": r["message"], "count": r["count"]} for r in rows]

    def count(self, table: str) -> int:
        if table not in ("samples", "samples_15m", "days", "decisions", "events"):
            raise ValueError(table)
        with self._lock:
            return self._db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

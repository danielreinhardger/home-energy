"""Error log: warnings and errors land in the database, repeats count up, old rows are pruned."""
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from homeenergy import eventlog
from homeenergy.store import EVENT_DAYS, Store

T = 1_794_000_000


class StoreEventTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_repeats_within_ten_minutes_count_up(self):
        for i in range(6):                                   # a device timing out every 10 s for a minute
            self.store.add_event(T + 10 * i, "warning", "polling", "kostal: timeout")
        self.store.add_event(T + 60 + 700, "warning", "polling", "kostal: timeout")   # 11 min later: new row
        rows = self.store.events()
        self.assertEqual([r["count"] for r in rows], [1, 6])
        self.assertLess(rows[1]["first"], rows[1]["last"])           # first and last occurrence kept

    def test_other_message_or_level_is_its_own_row(self):
        self.store.add_event(T, "warning", "polling", "kostal: timeout")
        self.store.add_event(T + 1, "info", "polling", "kostal: antwortet wieder")
        self.store.add_event(T + 2, "warning", "polling", "kostal: timeout")
        self.assertEqual([r["message"] for r in self.store.events()],
                         ["kostal: timeout", "kostal: antwortet wieder"])
        self.assertEqual(len(self.store.events(levels=("warning", "error"))), 1)

    def test_pruned_after_the_retention(self):
        self.store.add_event(T, "error", "web", "old")
        self.store.add_event(T + EVENT_DAYS * 86400 - 10, "error", "web", "new")
        self.assertEqual(self.store.prune(now=T + EVENT_DAYS * 86400 + 1)["events"], 1)
        self.assertEqual([r["message"] for r in self.store.events()], ["new"])

    def test_existing_database_gets_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite"
            db = sqlite3.connect(path)
            db.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); PRAGMA user_version = 1;")
            db.close()
            s = Store(path)
            s.add_event(T, "error", "x", "y")
            self.assertEqual(s.count("events"), 1)
            s.close()


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.handler = eventlog.install(self.store)
        self.log = logging.getLogger("homeenergy.testing")

    def tearDown(self):
        eventlog.uninstall(self.handler)

    def test_keeps_warnings_errors_and_marked_info(self):
        self.log.info("routine")                                    # not kept
        self.log.warning("kostal: timeout")
        self.log.info("kostal: antwortet wieder", extra=eventlog.EVENT)
        try:
            raise ValueError("bad value")
        except ValueError:
            self.log.exception("POST /api/x")
        rows = self.store.events()
        self.assertEqual([(r["level"], r["source"]) for r in rows],
                         [("error", "testing"), ("info", "testing"), ("warning", "testing")])
        self.assertEqual(rows[0]["message"], "POST /api/x (ValueError: bad value)")

    def test_a_broken_store_does_not_break_logging(self):
        self.store.close()
        self.log.warning("still fine")                             # must not raise


if __name__ == "__main__":
    unittest.main()

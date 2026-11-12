"""SQLite store: samples, 15-minute rollups, retention, ranges, ledger days, decisions, import."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from homeenergy.migrate import import_legacy
from homeenergy.store import RAW_DAYS, Store, iso, to_ts

T0 = to_ts("2026-11-09T10:00:00+01:00")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")

    def tearDown(self):
        self.s.close()

    def fill(self, minutes, start=T0, **extra):
        self.s.add_samples((start + 60 * i, {"pv_w": float(i), "house_w": 500.0, "outside_temp": 5.0 + i % 3, **extra})
                           for i in range(minutes))

    def test_samples_round_trip_and_new_columns(self):
        self.fill(3)
        self.s.add_sample(T0 + 180, {"pv_w": 9.0, "hp_w": 700.0, "note": "x", "flag": True})   # text/bool skipped
        rows = self.s.samples(T0 - 1)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["t"], iso(T0))
        self.assertEqual(rows[-1]["hp_w"], 700.0)
        self.assertIsNone(rows[0]["hp_w"])                       # added later: empty for older rows
        self.assertEqual(len(self.s.samples(T0)), 3)              # strictly after `since`
        with self.assertRaises(ValueError):
            self.s.add_sample(T0, {"bad name": 1.0})

    def test_same_minute_replaces(self):
        self.s.add_sample(T0, {"pv_w": 1.0})
        self.s.add_sample(T0, {"pv_w": 2.0})
        self.assertEqual([r["pv_w"] for r in self.s.samples(T0 - 1)], [2.0])

    def test_rollup_15_minutes_with_min_max(self):
        self.fill(60)                                             # 10:00–10:59
        self.assertEqual(self.s.rollup(now=T0 + 3600), 4)
        rows = self.s._db.execute("SELECT * FROM samples_15m ORDER BY ts").fetchall()
        self.assertEqual(rows[0]["n"], 15)
        self.assertAlmostEqual(rows[0]["pv_w"], 7.0)              # mean of 0..14
        self.assertEqual((rows[0]["outside_temp_min"], rows[0]["outside_temp_max"]), (5.0, 7.0))
        self.assertEqual(self.s.rollup(now=T0 + 3600), 0)         # nothing new

    def test_unfinished_bucket_waits(self):
        self.fill(20)
        self.s.rollup(now=T0 + 20 * 60)                          # 10:15–10:30 still running
        self.assertEqual(self.s.count("samples_15m"), 1)

    def test_prune_keeps_unrolled_minutes(self):
        old = T0 - (RAW_DAYS + 1) * 86400
        self.fill(30, start=old)
        self.assertEqual(self.s.prune(now=T0)["samples"], 0)     # not rolled up yet: kept
        self.s.rollup(now=old + 3600)
        self.assertEqual(self.s.prune(now=T0)["samples"], 30)
        self.assertEqual(self.s.count("samples_15m"), 2)          # the averages stay

    def test_series_picks_resolution(self):
        self.fill(6 * 60)
        short = self.s.series(T0, T0 + 3600, points=800)
        self.assertEqual((short["source"], short["resolution_s"]), ("minute", 60))
        thinned = self.s.series(T0, T0 + 6 * 3600, points=36)
        self.assertEqual(thinned["resolution_s"], 600)
        self.assertLessEqual(len(thinned["samples"]), 37)
        self.s.rollup(now=T0 + 6 * 3600)
        long = self.s.series(T0, T0 + 30 * 86400, points=500)
        self.assertEqual(long["source"], "15min")
        self.assertGreaterEqual(long["resolution_s"], 900)

    def test_days_and_decisions(self):
        self.s.save_days({"2026-11-09": {"day": "2026-11-09", "import_kwh": 2.5}})
        self.assertEqual(self.s.load_days()["2026-11-09"]["import_kwh"], 2.5)
        for m in range(3):
            self.s.add_decision("dhw", {"time": f"2026-11-09T20:3{m}:00+01:00", "mode": "save", "target": 42.0,
                                        "action": "unverändert"})
        self.s.add_decision("heating", {"time": "2026-11-09T20:35:00+01:00", "mode": "normal", "target": 22.0})
        recent = self.s.recent_decisions("dhw", 2)
        self.assertEqual([r["time"][-14:-6] for r in recent], ["20:32:00", "20:31:00"])   # newest first
        self.assertEqual(self.s.count("decisions"), 4)

    def test_meta(self):
        self.assertIsNone(self.s.get_meta("x"))
        self.s.set_meta("x", {"a": 1})
        self.assertEqual(self.s.get_meta("x"), {"a": 1})


class FileTests(unittest.TestCase):
    def test_backup_is_a_readable_copy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            s = Store(Path(d) / "db.sqlite")
            s.add_sample(T0, {"pv_w": 5.0})
            target = Path(d) / "backup" / "copy.sqlite"
            s.backup(target)
            s.close()
            con = sqlite3.connect(target)
            self.assertEqual(con.execute("SELECT pv_w FROM samples").fetchone()[0], 5.0)
            con.close()

    def test_legacy_import_once(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            data = Path(d)
            (data / "history.jsonl").write_text(
                "".join(json.dumps({"t": f"2026-11-09T10:0{i}:00+01:00", "pv_w": i}) + "\n" for i in range(5)) + "broken\n",
                encoding="utf-8")
            (data / "finance.json").write_text(json.dumps(
                {"days": [{"day": "2026-11-08", "import_kwh": 4.5}], "last_raw": {"import_kwh": 4.5}}), encoding="utf-8")
            (data / "decisions.jsonl").write_text(json.dumps({"time": "2026-11-09T20:38:00+01:00", "mode": "save"}) + "\n",
                                                  encoding="utf-8")
            s = Store(data / "db.sqlite")
            done = import_legacy(s, data)
            self.assertEqual((done["samples"], done["days"], done["decisions_dhw"]), (5, 1, 1))
            self.assertEqual(import_legacy(s, data), {})                  # only once
            self.assertEqual(s.count("samples"), 5)
            self.assertEqual(s.get_meta("last_raw"), {"import_kwh": 4.5})
            self.assertFalse((data / "history.jsonl").exists())           # moved out of the live folder …
            self.assertTrue((data / "backup" / "legacy-json" / "history.jsonl").exists())   # … kept as a backup
            s.close()

    def test_failed_batch_leaves_nothing(self):
        s = Store(":memory:")
        with self.assertRaises(ValueError):                              # bad name in the 2nd row
            s.add_samples([(1000, {"pv_w": 1.0}), (1060, {"Bad Name": 2.0})])
        self.assertEqual(s.count("samples"), 0)                          # the 1st row was rolled back too
        s.add_samples([(1000, {"pv_w": 1.0})])                           # and the store still works
        self.assertEqual(s.count("samples"), 1)
        s.close()

    def test_retired_column_dropped_on_upgrade(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "db.sqlite"
            con = sqlite3.connect(path)                                    # a schema-2 file with room_temp
            con.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE samples (ts INTEGER PRIMARY KEY, pv_w REAL, room_temp REAL);
                CREATE TABLE samples_15m (ts INTEGER PRIMARY KEY, n INTEGER NOT NULL, pv_w REAL,
                                          room_temp REAL, room_temp_min REAL, room_temp_max REAL);
                CREATE TABLE days (day TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE decisions (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL,
                                        mode TEXT, target REAL, action TEXT, entry TEXT NOT NULL);
                CREATE TABLE events (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, last_ts INTEGER NOT NULL,
                                     level TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL,
                                     count INTEGER NOT NULL DEFAULT 1);
                INSERT INTO samples VALUES (1000, 5.0, 20.0);
                PRAGMA user_version = 2;""")
            con.close()
            s = Store(path)
            self.assertEqual(s.value_columns, ["pv_w"])
            self.assertEqual(s._table_columns("samples_15m"), ["ts", "n", "pv_w"])
            self.assertEqual(s.samples(0)[0]["pv_w"], 5.0)                 # the other values stay
            s.add_sample(1060, {"pv_w": 6.0, "room_temp": 21.0})           # never created again
            self.assertEqual(s.value_columns, ["pv_w"])
            s.close()


if __name__ == "__main__":
    unittest.main()

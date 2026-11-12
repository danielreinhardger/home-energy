"""Runtime behaviour: device isolation, stale data, incremental history, log limits, gzip."""
import gzip
import json
import tempfile
import threading
import time
from datetime import datetime
import unittest
import urllib.request
from pathlib import Path

from homeenergy import views
from homeenergy import service as service_mod
from homeenergy.device import ModbusDevice
from homeenergy.devices import SPECS
from homeenergy.service import Service
from homeenergy.store import to_ts
from homeenergy.web import serve

from fakes import FakeRegisters


def make_service(tmp: Path) -> Service:
    devices = {"isg": ModbusDevice(SPECS["isg"], "fake", client=FakeRegisters()),
               "kostal": ModbusDevice(SPECS["kostal"], "fake", client=FakeRegisters())}
    return Service(tmp / "config.json", tmp / "data", devices=devices)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = Path(self._tmp.name)
        self.s = make_service(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_failing_device_does_not_block_the_others(self):
        class Broken:
            spec = SPECS["kostal"]
            host = "x"
            def read_values(self):
                raise OSError("timeout")
        self.s.poller.devices["kostal"] = Broken()
        self.s.poller.read("kostal")
        self.s.poller.read("isg")
        self.assertIsNotNone(self.s.poller.status["kostal"].error)
        self.assertIsNone(self.s.poller.status["isg"].error)
        self.assertEqual(self.s.poller.status["kostal"].failures, 1)
        self.assertIsNotNone(self.s.poller.status["isg"].read_ms)

    def test_control_skips_stale_data(self):
        self.s.poll_once()
        self.s.poller.status["kostal"].updated = time.time() - 600          # ten minutes old
        before = self.s.control.skipped
        self.s.control.run(self.s.poller, None, self.s.cop_model())
        self.assertEqual(self.s.control.skipped, before + 1)
        self.assertTrue(views.snapshot(self.s)["devices"]["kostal"]["stale"])

    def test_incremental_history(self):
        for t in ("2026-11-09T10:00:00+01:00", "2026-11-09T10:01:00+01:00", "2026-11-09T10:02:00+01:00"):
            self.s.store.add_sample(to_ts(t), {"pv_w": 1.0})
        newer = views.history(self.s, "2026-11-09T10:00:00+01:00")
        self.assertEqual([d["t"][-14:-6] for d in newer], ["10:01:00", "10:02:00"])
        self.assertEqual(len(views.history(self.s, "2026-11-09T10:00:00 01:00")), 2)   # unencoded "+"
        with self.assertRaises(ValueError):
            views.history(self.s, "gestern")

    def test_perf_view(self):
        self.s.poll_once()
        p = views.perf(self.s)
        self.assertIn("kostal", p["devices"])
        self.assertIsNotNone(p["devices"]["isg"]["read_ms"])
        json.dumps(p)


class HttpTests(unittest.TestCase):
    def test_gzip_and_since(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            s = make_service(Path(d))
            s.clock = lambda: datetime.fromisoformat("2026-11-09T11:00:00+01:00")
            s.store.add_samples((to_ts(f"2026-11-09T10:{i:02d}:00+01:00"), {"pv_w": float(i), "house_w": 500.0})
                                for i in range(60))
            httpd = serve(s, "127.0.0.1", 0, Path(d))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                req = urllib.request.Request(base + "/api/history", headers={"Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(r.headers["Content-Encoding"], "gzip")
                    self.assertEqual(len(json.loads(gzip.decompress(r.read()))), 60)
                with urllib.request.urlopen(base + "/api/history?since=2026-11-09T10%3A57%3A00%2B01%3A00") as r:
                    self.assertEqual(len(json.loads(r.read())), 2)
                with urllib.request.urlopen(base + "/api/history?from=2026-11-09T10%3A00%3A00%2B01%3A00&points=50") as r:
                    body = json.loads(r.read())
                    self.assertEqual(body["resolution_s"], 120)      # 1 h in ≤ 50 points → 2-min buckets
                    self.assertEqual(len(body["samples"]), 30)
                with urllib.request.urlopen(base + "/api/perf") as r:
                    self.assertIn("/api/history", json.loads(r.read())["api"])
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()

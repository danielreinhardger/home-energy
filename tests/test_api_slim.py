"""The page's data diet: a slim state every 10 s, labels and rule texts once (meta),
decision logs on demand, history as columns."""
import json
import tempfile
import unittest
from pathlib import Path

from homeenergy import views
from homeenergy.store import Store

from test_runtime import make_service

T0 = 1_794_000_000


class SlimStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.s = make_service(Path(self._tmp.name))
        self.s.poll_once()
        self.s.control.run(self.s.poller, None, self.s.cop_model())

    def tearDown(self):
        self._tmp.cleanup()

    def test_state_carries_values_not_labels_or_logs(self):
        st = views.snapshot(self.s)
        vals = st["devices"]["isg"]["values"]
        self.assertIsInstance(vals, dict)
        self.assertIn("dhw_temp", vals)
        for part in ("controller", "heating"):
            self.assertNotIn("recent", st[part])
            self.assertNotIn("explain", st[part])
        self.assertEqual(st["meta_version"], views.meta(self.s)["version"])

    def test_meta_has_labels_and_rules_and_follows_the_config(self):
        m = views.meta(self.s)
        isg = {p["key"]: p for p in m["devices"]["isg"]}
        self.assertEqual(isg["dhw_temp"]["unit"], "°C")
        self.assertIn("stages", m["explain"]["dhw"])
        self.assertIs(views.meta(self.s), m)                                   # cached while the config is the same
        self.s.update_config("dhw", {"comfort_temp": 47.5})
        self.assertNotEqual(views.meta(self.s)["version"], m["version"])     # rule texts contain settings

    def test_decisions_are_slim(self):
        d = views.decisions(self.s)
        self.assertEqual(set(d), {"dhw", "heating"})
        for entry in d["dhw"] + d["heating"]:
            self.assertEqual(set(entry), {"time", "mode", "target", "device_target", "action", "reasons"})

    def test_labels_and_rule_texts_travel_only_in_meta(self):
        state = json.dumps(views.snapshot(self.s), ensure_ascii=False)
        meta = json.dumps(views.meta(self.s), ensure_ascii=False)
        label = next(p["label"] for p in views.meta(self.s)["devices"]["isg"] if p["key"] == "dhw_temp")
        self.assertIn(label, meta)
        self.assertNotIn(f'"{label}"', state)
        self.assertGreater(len(meta), 5000)      # what no longer travels every 10 s


class ColumnsTests(unittest.TestCase):
    def test_columns_round_and_drop_empty(self):
        s = Store(":memory:")
        s.add_samples([(T0 + 60 * i, {"pv_w": 1234.56 + i, "dhw_temp": 45.04, "car_w": None}) for i in range(3)])
        c = s.samples(T0 - 1, columns=True)
        self.assertEqual(c["t"], [T0, T0 + 60, T0 + 120])
        self.assertEqual(c["pv_w"], [1235, 1236, 1237])            # watts: whole numbers
        self.assertEqual(c["dhw_temp"], [45.0, 45.0, 45.0])
        self.assertNotIn("car_w", c)                               # no value at all: left out
        rows = s.samples(T0 - 1)
        self.assertEqual(len(rows), 3)                             # the row format still works

    def test_series_columns(self):
        s = Store(":memory:")
        s.add_samples([(T0 + 60 * i, {"pv_w": float(i)}) for i in range(120)])
        res = s.series(T0, T0 + 7200, points=10, columns=True)
        self.assertEqual(len(res["samples"]["t"]), len(res["samples"]["pv_w"]))
        self.assertLessEqual(len(res["samples"]["t"]), 12)

    def test_since_accepts_unix_seconds(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            svc = make_service(Path(tmp))
            svc.store.add_samples([(T0 + 60 * i, {"pv_w": 1.0}) for i in range(3)])
            self.assertEqual(views.history(svc, str(T0 + 60), columns=True)["t"], [T0 + 120])


class PointMetaTests(unittest.TestCase):
    def test_every_device_kind(self):
        # the test service has no openWB or ISG web page: check their point kinds here
        from homeenergy.devices import isg_web, kostal, openwb
        for points in (kostal.POINTS, openwb.POINTS, isg_web.POINTS):
            for pt in points:
                self.assertEqual(views.point_meta(pt)["key"], pt.key)

    def test_status_texts(self):
        from homeenergy.devices import kostal
        state = next(views.point_meta(p) for p in kostal.POINTS if p.key == "state")
        self.assertEqual(state["options"]["6"], "Einspeisen")


if __name__ == "__main__":
    unittest.main()

"""The explanation steps (hotwater.stages(), heating.stages()) cover exactly the checks the rules make."""
import unittest
from datetime import datetime, timedelta, timezone

from homeenergy import heating, hotwater, simulate
from homeenergy.hotwater import BathRequest, DhwConfig, DhwInputs, DhwState
from homeenergy.situation import Situation

TZ = timezone(timedelta(hours=1))
SWITCHES = {"enabled", "sensor", "h_enabled"}         # "switched off" / no sensor: shown as the off text


def stage_nodes(stages):
    nodes = set()
    for s in stages:
        nodes.add(s["entry"])
        if "gate" in s:
            nodes.add(s["gate"])
        nodes.update(c[0] for c in s["checks"])
    return nodes


def traced_nodes():
    """Every trace node the strategies record over the model days plus a bath."""
    seen = {"dhw": set(), "heating": set()}
    orig_h, orig_d = simulate.decide_heating, simulate.decide

    def h(*a, **k):
        d, st = orig_h(*a, **k)
        seen["heating"].update(r["node"] for r in d.trace)
        return d, st

    def w(*a, **k):
        d, st = orig_d(*a, **k)
        seen["dhw"].update(r["node"] for r in d.trace)
        return d, st

    simulate.decide_heating, simulate.decide = h, w
    try:
        for s in simulate.SCENARIOS:
            simulate.run(s, True)
    finally:
        simulate.decide_heating, simulate.decide = orig_h, orig_d
    day = datetime(2026, 11, 9, 0, 0, tzinfo=TZ)
    bath = BathRequest(day.replace(hour=17, minute=30), 55.0)
    state = DhwState()
    for hour in (9, 12, 16, 17):
        for grid in (300.0, -2500.0):
            sit = Situation(day.replace(hour=hour), 3000, grid, 0, 80, 100, 10240, dhw_temp=45.0)
            d, state = hotwater.decide(DhwInputs(sit, bath=bath), DhwConfig(), state)
            seen["dhw"].update(r["node"] for r in d.trace)
    return seen


class ExplainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = traced_nodes()

    def check(self, rules, cfg, seen):
        known = stage_nodes(rules.stages(cfg)) | set(rules.OUTCOMES) | SWITCHES
        self.assertEqual(seen - known, set(), "checks without an explanation step – add them to stages()")
        steps = stage_nodes(rules.stages(cfg))
        self.assertEqual(steps - seen, set(), "explanation steps no rule checks (renamed or removed?)")

    def test_hot_water(self):
        self.check(hotwater, DhwConfig(), self.seen["dhw"])

    def test_heating(self):
        self.check(heating, heating.HeatingConfig(), self.seen["heating"])

    def test_every_mode_has_a_label(self):
        self.assertEqual(set(hotwater.MODE_LABELS), set(hotwater.DhwMode))
        self.assertEqual(set(heating.MODE_LABELS), set(heating.HeatMode))


if __name__ == "__main__":
    unittest.main()

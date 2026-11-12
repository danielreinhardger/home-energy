import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.hotwater import (BathRequest, DhwConfig, DhwInputs, DhwMode as Mode, DhwState, bath_plan, decide,
                                 next_occurrence, parse_hhmm)
from homeenergy.situation import Situation

TZ = timezone(timedelta(hours=1))
DAY = datetime(2026, 11, 9, 0, 0, tzinfo=TZ)
BATH = BathRequest(DAY.replace(hour=17, minute=30), 55.0)


def inp(hour, minute=0, grid=0.0, battery=0.0, soc=80.0, dhw=45.0, bath=BATH):
    sit = Situation(DAY.replace(hour=hour, minute=minute), 0, grid, battery, soc, 100, 10240, dhw_temp=dhw)
    return DhwInputs(sit, bath=bath)


def run(steps, state=None, cfg=None):
    cfg = cfg or DhwConfig()
    state = state or DhwState()
    d = None
    for i in steps:
        d, state = decide(i, cfg, state)
    return d, state


class BathPlanTests(unittest.TestCase):
    def test_latest_start(self):
        # 235 L 45->55 °C = 1.05 kWh el at 1300 W = 48.5 min; *1.3 + 20 min margin = 83 min before 17:30
        plan = bath_plan(BATH, 45.0, DhwConfig())
        self.assertAlmostEqual(plan["heat_min"], 48.5, delta=0.5)
        self.assertEqual(plan["latest_start"].strftime("%H:%M"), "16:06")

    def test_next_occurrence_and_parsing(self):
        now = DAY.replace(hour=18)
        self.assertEqual(next_occurrence(now, "17:30"), DAY.replace(hour=17, minute=30) + timedelta(days=1))
        self.assertEqual(next_occurrence(DAY.replace(hour=9), "17:30"), DAY.replace(hour=17, minute=30))
        with self.assertRaises(ValueError):
            parse_hhmm("25:00")
        with self.assertRaises(ValueError):
            parse_hhmm("abc")


class BathDecisionTests(unittest.TestCase):
    def test_waits_for_pv_in_the_morning(self):
        d, _ = decide(inp(9, grid=200), DhwConfig(), DhwState())
        self.assertNotEqual(d.mode, Mode.BATH)
        self.assertIn("wartet auf PV", d.reasons[0])
        self.assertEqual(d.metrics["bath"]["latest_start"], "16:06")

    def test_uses_surplus_even_when_battery_not_full(self):
        # battery at 60 % charging 2 kW: without a bath this would be "battery first"
        steps = [inp(12, i, grid=0, battery=-2000, soc=60) for i in range(6)]
        d, s = run(steps)
        self.assertEqual((d.mode, d.target), (Mode.BATH, 55.0))
        self.assertIn("PV-Überschuss", d.reasons[0])

    def test_forces_heating_at_latest_start(self):
        d, _ = decide(inp(16, 10, grid=300, battery=800), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.BATH, 55.0))
        self.assertIn("spätester Start", d.reasons[0])

    def test_pauses_when_clouds_stay_and_resumes_later(self):
        _, s = run([inp(12, i, grid=-2000) for i in range(6)])
        self.assertEqual(s.mode, Mode.BATH)
        steps = [inp(12, 30 + i, grid=800) for i in range(12)]   # 11 min deficit, boost 30 min old
        d, s = run(steps, state=s)
        self.assertNotEqual(d.mode, Mode.BATH)
        self.assertIn("wartet auf PV", d.reasons[0])

    def test_ready_tank_is_held(self):
        d, _ = decide(inp(15, dhw=54.5), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.BATH, 55.0))
        self.assertIn("bereit", d.reasons[0])

    def test_request_expires_after_deadline(self):
        d, _ = decide(inp(18, grid=300), DhwConfig(), DhwState(Mode.BATH, DAY.replace(hour=16)))
        self.assertNotEqual(d.mode, Mode.BATH)

    def test_bath_takes_over_a_running_pv_reheat(self):
        _, s = run([inp(12, i, grid=-2000, soc=100, dhw=40, bath=None) for i in range(6)])
        self.assertEqual(s.mode, Mode.REHEAT)
        d, _ = decide(inp(12, 7, grid=-2000, soc=100, dhw=40), DhwConfig(), s)
        self.assertEqual((d.mode, d.target), (Mode.BATH, 55.0))


if __name__ == "__main__":
    unittest.main()

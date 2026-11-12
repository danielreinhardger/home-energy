import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.forecast import ForecastSummary
from homeenergy.hotwater import DhwConfig, DhwInputs, DhwMode as Mode, DhwState, decide, dhw_energy_wh
from homeenergy.situation import Situation

TZ = timezone(timedelta(hours=2))
NOON = datetime(2026, 6, 1, 12, 0, tzinfo=TZ)
RECENT = NOON - timedelta(days=1)                         # last anti-legionella run


def fc(remaining_kwh=20.0, tomorrow_kwh=30.0, sun_hours=6.0):
    return ForecastSummary(NOON, 1.0, remaining_kwh * 1000, 40000, tomorrow_kwh * 1000, sun_hours, [])


def inputs(now=NOON, grid=-2000.0, battery=0.0, soc=100.0, dhw=40.0, forecast=None, season=False, heat_boost=False):
    sit = Situation(now, pv_w=4000, grid_w=grid, battery_w=battery, soc=soc, max_soc=100,
                    battery_capacity_wh=10240, dhw_temp=dhw, forecast=forecast)
    return DhwInputs(sit, heating_season=season, heating_boost=heat_boost)


def run(steps, cfg=None, state=None):
    """Feed a list of inputs one minute apart; return the last decision and state."""
    cfg = cfg or DhwConfig()
    state = state or DhwState(hygiene_at=RECENT)          # anti-legionella not due (own tests below)
    decision = None
    for inp in steps:
        decision, state = decide(inp, cfg, state)
    return decision, state


def minutes(n, start=0, **kw):
    return [inputs(now=NOON + timedelta(minutes=start + i), **kw) for i in range(n)]


class HygieneTests(unittest.TestCase):
    """Anti-legionella: 60 °C once a week, preferably with PV; overdue -> midday anyway."""

    def test_due_with_pv_heats_to_60(self):
        st = DhwState(hygiene_at=NOON - timedelta(days=8))
        d, st = run(minutes(6, dhw=47), state=st)                       # tank above the daily need
        self.assertEqual((d.mode, d.target), (Mode.HYGIENE, 60.0))
        d, st = run(minutes(2, start=30, dhw=59.2), state=st)           # reached (within 1 K)
        self.assertNotEqual(d.mode, Mode.HYGIENE)
        self.assertEqual(st.hygiene_at, NOON + timedelta(minutes=31))

    def test_not_due_does_nothing(self):
        d, _ = run(minutes(6, dhw=47), state=DhwState(hygiene_at=NOON - timedelta(days=2)))
        self.assertNotEqual(d.mode, Mode.HYGIENE)

    def test_overdue_runs_at_midday_without_pv(self):
        st = DhwState(hygiene_at=NOON - timedelta(days=15))
        d, st = decide(inputs(grid=800, soc=30, dhw=45, forecast=fc(remaining_kwh=1, tomorrow_kwh=2)), DhwConfig(), st)
        self.assertEqual(d.mode, Mode.HYGIENE)
        self.assertIn("kein PV-Tag", d.reasons[0])
        evening = NOON.replace(hour=19)
        d, _ = decide(inputs(now=evening, grid=800, soc=30, dhw=45), DhwConfig(),
                      DhwState(hygiene_at=evening - timedelta(days=15)))
        self.assertNotEqual(d.mode, Mode.HYGIENE)                       # not in the cold evening

    def test_gives_up_after_max_minutes_and_waits_an_interval(self):
        st = DhwState(Mode.HYGIENE, NOON, hygiene_at=NOON - timedelta(days=15))
        d, st = decide(inputs(now=NOON + timedelta(minutes=241), dhw=55), DhwConfig(), st)
        self.assertNotEqual(d.mode, Mode.HYGIENE)
        self.assertIn("nicht erreicht", d.reasons[0])
        self.assertEqual(st.hygiene_at, NOON + timedelta(minutes=241))

    def test_a_hot_bath_counts(self):
        _, st = decide(inputs(grid=300, dhw=60.5), DhwConfig(), DhwState(hygiene_at=NOON - timedelta(days=6)))
        self.assertEqual(st.hygiene_at, NOON)

    def test_switched_off(self):
        d, _ = run(minutes(6, dhw=47), cfg=DhwConfig(hygiene_enabled=False),
                   state=DhwState(hygiene_at=NOON - timedelta(days=30)))
        self.assertNotEqual(d.mode, Mode.HYGIENE)

    def test_state_survives_a_restart(self):
        st = DhwState(hygiene_at=NOON)
        self.assertEqual(DhwState.from_json(st.to_json()).hygiene_at, NOON)


class DailyNeedTests(unittest.TestCase):
    """Daily need: PV heats the tank to comfort, battery doesn't have to be full."""

    def test_reheat_needs_sustained_surplus(self):
        d, _ = run(minutes(3))
        self.assertNotEqual(d.mode, Mode.REHEAT)
        d, _ = run(minutes(6))
        self.assertEqual((d.mode, d.target), (Mode.REHEAT, 46.0))

    def test_battery_first_no_reheat_while_battery_needs_the_pv(self):
        # battery at 60 % taking all the surplus and no forecast: the battery comes first
        d, _ = run(minutes(10, grid=0, battery=-2000, soc=60))
        self.assertNotEqual(d.mode, Mode.REHEAT)

    def test_reheat_with_export_or_when_the_battery_fills_anyway(self):
        d, _ = run(minutes(6, grid=-1500, battery=-2000, soc=60))                       # still exporting
        self.assertEqual(d.mode, Mode.REHEAT)
        d, _ = run(minutes(6, grid=0, battery=-2000, soc=60, forecast=fc(remaining_kwh=20)))
        self.assertEqual(d.mode, Mode.REHEAT)                                         # forecast fills it

    def test_battery_first_can_be_switched_off(self):
        d, _ = run(minutes(6, grid=0, battery=-2000, soc=60), cfg=DhwConfig(battery_first=False))
        self.assertEqual(d.mode, Mode.REHEAT)

    def test_no_reheat_when_tank_is_fine(self):
        d, _ = run(minutes(10, dhw=45.0))
        self.assertNotIn(d.mode, (Mode.REHEAT, Mode.BOOST))


class ReserveTests(unittest.TestCase):
    """Reserve above comfort only before a dull day (no more daily 55 °C)."""

    def test_no_reserve_before_a_sunny_day(self):
        d, _ = run(minutes(10, dhw=46.0, forecast=fc(tomorrow_kwh=30)))
        self.assertNotEqual(d.mode, Mode.BOOST)

    def test_reserve_before_a_dull_day(self):
        d, _ = run(minutes(6, dhw=46.0, forecast=fc(tomorrow_kwh=3)))
        self.assertEqual((d.mode, d.target), (Mode.BOOST, 50.0))

    def test_reserve_waits_for_the_battery(self):
        d, _ = run(minutes(10, dhw=46.0, grid=0, battery=-2000, soc=60, forecast=fc(remaining_kwh=3, tomorrow_kwh=3)))
        self.assertNotEqual(d.mode, Mode.BOOST)

    def test_winter_house_preheating_comes_first(self):
        steps = minutes(10, dhw=46.0, forecast=fc(tomorrow_kwh=3), season=True, heat_boost=True)
        d, _ = run(steps)
        self.assertNotEqual(d.mode, Mode.BOOST)
        d, _ = run(minutes(6, dhw=46.0, forecast=fc(tomorrow_kwh=3), season=True, heat_boost=False))
        self.assertEqual(d.mode, Mode.BOOST)


class StopTests(unittest.TestCase):
    def reheating(self):
        _, state = run(minutes(6))
        self.assertEqual(state.mode, Mode.REHEAT)
        return state

    def test_stops_when_tank_reaches_target(self):
        d, _ = decide(inputs(now=NOON + timedelta(minutes=30), dhw=45.5), DhwConfig(), self.reheating())
        self.assertNotEqual(d.mode, Mode.REHEAT)
        self.assertIn("geladen", d.reasons[0])

    def test_short_cloud_does_not_stop(self):
        d, _ = run(minutes(5, start=30, grid=800), state=self.reheating())
        self.assertEqual(d.mode, Mode.REHEAT)

    def test_sustained_deficit_stops_after_min_runtime(self):
        d, _ = run(minutes(12, start=30, grid=800), state=self.reheating())
        self.assertNotEqual(d.mode, Mode.REHEAT)

    def test_min_runtime_holds_even_with_deficit(self):
        d, _ = run(minutes(12, start=6, grid=800), state=self.reheating())   # deficit 11 min, run only 17 min
        self.assertEqual(d.mode, Mode.REHEAT)


class BaselineTests(unittest.TestCase):
    def test_solar_wait_in_the_morning_with_good_forecast(self):
        morning = NOON.replace(hour=7)
        d, _ = decide(inputs(now=morning, grid=300, dhw=43, forecast=fc(remaining_kwh=25)), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.SOLAR_WAIT, 42.0))

    def test_evening_waits_for_sunny_tomorrow(self):
        evening = NOON.replace(hour=20)
        d, _ = decide(inputs(now=evening, grid=0, battery=600, soc=70,
                             forecast=fc(remaining_kwh=0, sun_hours=0, tomorrow_kwh=25)), DhwConfig(), DhwState())
        self.assertEqual(d.mode, Mode.SOLAR_WAIT)

    def test_evening_before_dull_day_drops_to_minimum(self):
        evening = NOON.replace(hour=20)
        d, _ = decide(inputs(now=evening, grid=0, battery=600, soc=70,
                             forecast=fc(remaining_kwh=0, sun_hours=0, tomorrow_kwh=3)), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.SAVE, 42.0))
        self.assertIn("Trüber Tag", d.reasons[0])

    def test_after_midnight_on_a_dull_day_still_minimum(self):
        night = NOON.replace(hour=2)
        dull = ForecastSummary(night, 1.0, 3000, 3000, 4000, 0.5, [])      # nothing of today's 3 kWh has come yet
        d, _ = decide(inputs(now=night, grid=0, battery=600, soc=70, forecast=dull), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.SAVE, 42.0))

    def test_dull_day_itself_holds_the_minimum(self):
        morning = NOON.replace(hour=10)
        dull = ForecastSummary(morning, 1.0, 1500, 3000, 3000, 0.5, [])    # half of the weak PV is done
        d, _ = decide(inputs(now=morning, grid=300, forecast=dull), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.PV_ONLY, 42.0))
        self.assertIn("Nur mit PV", d.reasons[0])

    def test_dull_day_reheats_to_comfort_without_pv_only(self):
        morning = NOON.replace(hour=10)
        dull = ForecastSummary(morning, 1.0, 1500, 3000, 3000, 0.5, [])
        d, _ = decide(inputs(now=morning, grid=300, forecast=dull), DhwConfig(pv_only=False), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.NORMAL, 46.0))

    def test_pv_surplus_still_heats_to_comfort(self):
        # pv_only only changes the target without PV; with surplus the daily reheat runs as before
        d, _ = run(minutes(6))
        self.assertEqual((d.mode, d.target), (Mode.REHEAT, 46.0))

    def test_evening_before_dull_day_keeps_comfort_when_switched_off(self):
        evening = NOON.replace(hour=20)
        d, _ = decide(inputs(now=evening, grid=0, battery=600, soc=70,
                             forecast=fc(remaining_kwh=0, sun_hours=0, tomorrow_kwh=3)),
                      DhwConfig(dull_night_min=False, pv_only=False), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.NORMAL, 46.0))

    def test_without_forecast_minimum(self):
        d, _ = decide(inputs(grid=300), DhwConfig(), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.PV_ONLY, 42.0))
        d, _ = decide(inputs(grid=300), DhwConfig(pv_only=False), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.NORMAL, 46.0))

    def test_disabled(self):
        d, _ = decide(inputs(), DhwConfig(enabled=False), DhwState())
        self.assertEqual((d.mode, d.target), (Mode.OFF, 46.0))


class EnergyTests(unittest.TestCase):
    def test_tank_energy(self):
        # 235 L from 45 to 55 °C = 2.73 kWh heat; at COP 2.6 about 1.05 kWh electricity
        self.assertAlmostEqual(dhw_energy_wh(DhwConfig(), 45, 55), 1051.2, delta=1)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            DhwConfig(min_temp=50, comfort_temp=46).validate()
        with self.assertRaises(ValueError):
            DhwConfig(boost_temp=45).validate()        # reserve below comfort


class TraceTests(unittest.TestCase):
    """The explanation tab relies on every decision ending in exactly one outcome node."""

    def outcome(self, d):
        outs = [r["node"] for r in d.trace if r["node"].startswith("out_")]
        self.assertEqual(len(outs), 1, d.trace)
        return outs[0]

    def test_every_path_ends_in_one_outcome(self):
        cases = [
            (minutes(6), "out_reheat"),
            (minutes(6, dhw=46.0, forecast=fc(tomorrow_kwh=3)), "out_boost"),
            (minutes(3), "out_pv_only"),
            ([inputs(grid=300, forecast=fc(remaining_kwh=25))], "out_wait"),
        ]
        for steps, expected in cases:
            d, _ = run(steps)
            self.assertEqual(self.outcome(d), expected)

    def test_trace_shows_why_there_is_no_reserve(self):
        d, _ = run(minutes(10, dhw=46.0, forecast=fc(tomorrow_kwh=30)))
        nodes = {r["node"]: r for r in d.trace}
        self.assertFalse(nodes["dull_tomorrow"]["ok"])
        self.assertFalse(nodes["need"]["ok"])


if __name__ == "__main__":
    unittest.main()

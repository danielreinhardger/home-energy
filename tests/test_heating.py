import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.controller import HeatingController, WriteLimits
from homeenergy.device import ModbusDevice
from homeenergy.devices import SPECS
from homeenergy.forecast import ForecastSummary
from homeenergy.heating import HeatingConfig, HeatingInputs, HeatMode as Mode, HeatState, battery_ok, buffer_target, decide_heating
from homeenergy.situation import Situation

from fakes import FakeRegisters

TZ = timezone(timedelta(hours=1))
NOON = datetime(2026, 1, 15, 12, 0, tzinfo=TZ)


def inp(minute=0, outside=2.0, room=21.5, grid=-2500.0, battery=0.0, soc=96.0, dhw=None, pv=4000.0,
        sun_left=None, tomorrow=None, at=NOON, free=None, need=0.0, comp=None):
    """`free` = free PV still today, `need` = what the battery still needs (both in Wh). A forecast
    exists once free, sun_left or tomorrow is given (default 4 sun hours left, 20 kWh tomorrow)."""
    now = at + timedelta(minutes=minute)
    fc = None
    if free is not None or sun_left is not None or tomorrow is not None:
        sun_left = 4.0 if sun_left is None else sun_left
        tomorrow = 20000.0 if tomorrow is None else tomorrow
        rest = (free or 0.0) + 500.0 * sun_left                      # base load 500 W comes off
        fc = ForecastSummary(now, 1.0, rest, rest, tomorrow, sun_left, [])
    sit = Situation(now, pv_w=pv, grid_w=grid, battery_w=battery, soc=soc, max_soc=soc + need / 100,
                    battery_capacity_wh=10000.0, outside_temp=outside, room_temp=room, compressor_w=comp, forecast=fc)
    return HeatingInputs(sit, dhw, 4.6)


def run(steps, cfg=None, state=None):
    cfg, state, d = cfg or HeatingConfig(), state or HeatState(), None
    for i in steps:
        d, state = decide_heating(i, cfg, state)
    return d, state


EVENING = NOON.replace(hour=19)


class FlipFlopTests(unittest.TestCase):
    """Seen live on 2026-11-11: 20 <-> 22 °C six times a day (clouds, forecast at 8 kWh, 15 °C outside)."""

    def evening(self, minute, tomorrow, outside=10.0):
        return inp(minute, outside=outside, grid=300, pv=0, sun_left=0, tomorrow=tomorrow, at=EVENING)

    def test_forecast_wobble_keeps_the_setback(self):
        steps = [self.evening(0, 8600), self.evening(60, 7900), self.evening(120, 8800), self.evening(180, 7300)]
        modes = []
        state, cfg = HeatState(), HeatingConfig()
        for s in steps:
            d, state = decide_heating(s, cfg, state)
            modes.append(d.mode)
        # 7.9 kWh stays a setback (margin to 6.8 kWh); 7.3 kWh too; no switching back and forth
        self.assertEqual(modes, [Mode.SETBACK] * 4)
        d, _ = decide_heating(self.evening(240, 6000), cfg, state)     # clearly dull: back to normal
        self.assertEqual(d.mode, Mode.NORMAL)

    def test_minimum_time_per_mode(self):
        cfg = HeatingConfig()
        d, state = decide_heating(self.evening(0, 9000), cfg, HeatState())
        self.assertEqual(d.mode, Mode.SETBACK)
        d, state = decide_heating(self.evening(20, 3000), cfg, state)  # forecast collapses after 20 min
        self.assertEqual((d.mode, d.target), (Mode.SETBACK, 20.0))
        self.assertIn("bleibt noch 25 min", d.reasons[0])
        d, state = decide_heating(self.evening(46, 3000), cfg, state)
        self.assertEqual((d.mode, d.target), (Mode.NORMAL, 22.0))

    def test_heating_season_has_a_margin(self):
        cfg = HeatingConfig()
        d, state = decide_heating(self.evening(0, 9000, outside=14.0), cfg, HeatState())
        self.assertEqual(d.mode, Mode.SETBACK)
        d, state = decide_heating(self.evening(60, 9000, outside=15.4), cfg, state)
        self.assertEqual(d.mode, Mode.SETBACK)                          # 15.4 < 15 + 1 K: still season
        d, state = decide_heating(self.evening(120, 9000, outside=16.2), cfg, state)
        self.assertEqual(d.mode, Mode.NORMAL)

    def test_clouds_do_not_toggle_battery_first(self):
        cfg = HeatingConfig()
        hold = lambda m, pv: inp(m, grid=0, battery=-pv, pv=pv, soc=40, free=3000, need=6000)
        d, state = decide_heating(hold(0, 1500), cfg, HeatState())
        self.assertEqual(d.mode, Mode.HOLD)
        d, state = decide_heating(hold(60, 200), cfg, state)            # a cloud: 200 W is still "PV running"
        self.assertEqual(d.mode, Mode.HOLD)

    def test_preheating_is_never_delayed(self):
        cfg = HeatingConfig()
        d, state = decide_heating(inp(0, grid=0, battery=-2500, soc=40, free=3000, need=6000), cfg, HeatState())
        self.assertEqual(d.mode, Mode.HOLD)
        for m in range(1, 12):                                         # battery full a few minutes later
            d, state = decide_heating(inp(m, soc=96), cfg, state)
        self.assertEqual(d.mode, Mode.BOOST)


class BatteryFirstTests(unittest.TestCase):
    """A full battery comes first: while it won't fill, no preheating – the house stays at normal."""

    def test_house_waits_while_the_battery_cannot_fill(self):
        # battery 40 % taking all the PV, forecast too weak to fill it
        d, _ = run([inp(i, grid=0, battery=-2500, soc=40, free=3000, need=6000) for i in range(20)])
        self.assertEqual((d.mode, d.target), (Mode.HOLD, 22.0))          # normal temperature, no preheating
        self.assertIn("Batterie zuerst", d.reasons[0])

    def test_preheats_when_the_forecast_fills_the_battery_anyway(self):
        d, _ = run([inp(i, grid=0, battery=-2500, soc=60, free=15000, need=4000) for i in range(11)])
        self.assertEqual(d.mode, Mode.BOOST)

    def test_full_battery_allows_preheating(self):
        d, _ = run([inp(i, soc=96) for i in range(11)])
        self.assertEqual(d.mode, Mode.BOOST)

    def test_no_hold_without_pv(self):
        evening = NOON.replace(hour=20)
        d, _ = decide_heating(inp(grid=300, pv=0, sun_left=0, soc=40, at=evening), HeatingConfig(), HeatState())
        self.assertNotEqual(d.mode, Mode.HOLD)

    def test_can_be_switched_off(self):
        d, _ = run([inp(i, grid=-1500, battery=-2500, soc=40, free=3000, need=6000) for i in range(11)],
                   cfg=HeatingConfig(battery_first=False))
        self.assertEqual(d.mode, Mode.BOOST)


class FineControlTests(unittest.TestCase):
    """Preheating starts gently and follows the surplus in small steps."""

    def boosting(self, room=21.5):
        d, s = run([inp(i, room=room) for i in range(11)])
        self.assertEqual(d.mode, Mode.BOOST)
        return d, s

    def test_gentle_start_just_above_the_room(self):
        d, _ = self.boosting(room=21.5)
        self.assertEqual(d.target, 21.8)                         # room + 0.3 K, not straight to the cap

    def test_steps_up_while_still_exporting(self):
        _, s = self.boosting()
        d, s = decide_heating(inp(25, grid=-800), HeatingConfig(), s)    # 15 min later, 800 W still exported
        self.assertEqual(d.target, 22.0)
        d, s = decide_heating(inp(28, grid=-800), HeatingConfig(), s)    # only 3 min later: no new step
        self.assertEqual(d.target, 22.0)

    def test_steps_down_when_drawing_from_grid(self):
        _, s = self.boosting()
        d, _ = decide_heating(inp(25, grid=400, pv=3000), HeatingConfig(), s)
        self.assertEqual(d.target, 21.6)

    def test_target_never_above_the_buffer_cap(self):
        _, s = self.boosting(room=21.5)
        cfg = HeatingConfig()
        for k in range(20):                                    # keeps exporting for hours
            d, s = decide_heating(inp(25 + 10 * k, grid=-2000, room=21.5), cfg, s)
        cap, _ = buffer_target(inp(free=None), cfg)
        self.assertLessEqual(d.target, cap)

    def test_room_warm_ends_preheating(self):
        _, s = self.boosting()
        d, _ = decide_heating(inp(20, room=23.5), HeatingConfig(), s)
        self.assertIn("Puffer voll", d.reasons[0])

    def test_sustained_deficit_ends_preheating(self):
        _, s = self.boosting()
        d, _ = run([inp(40 + i, grid=600, pv=500) for i in range(16)], state=s)
        self.assertNotEqual(d.mode, Mode.BOOST)


class BufferTests(unittest.TestCase):
    def test_plenty_of_sun_fills_the_buffer(self):
        # full 2 K buffer needs 2 × 20 kWh/K ÷ 4.6 ≈ 8.7 kWh electricity, after the battery's need
        self.assertEqual(buffer_target(inp(free=12000), HeatingConfig())[0], 24.0)

    def test_battery_need_comes_off_first(self):
        self.assertLess(buffer_target(inp(free=12000, need=8000), HeatingConfig())[0], 24.0)

    def test_little_sun_small_step(self):
        self.assertEqual(buffer_target(inp(free=500), HeatingConfig())[0], 22.3)   # at least +0.3 K

    def test_dull_tomorrow_uses_the_full_buffer(self):
        self.assertEqual(buffer_target(inp(free=2000, tomorrow=3000), HeatingConfig())[0], 24.0)

    def test_battery_ok(self):
        self.assertTrue(battery_ok(inp(soc=96), HeatingConfig())[0])
        self.assertTrue(battery_ok(inp(soc=50, free=10000, need=5000), HeatingConfig())[0])
        self.assertFalse(battery_ok(inp(soc=50, free=5000, need=5000), HeatingConfig())[0])     # no safety margin


class SetbackTests(unittest.TestCase):
    def test_evening_setback_before_a_sunny_day(self):
        evening = NOON.replace(hour=20)
        d, _ = decide_heating(inp(grid=300, battery=800, pv=0, sun_left=0, tomorrow=15000, at=evening),
                              HeatingConfig(), HeatState())
        self.assertEqual((d.mode, d.target), (Mode.SETBACK, 20.0))

    def test_no_setback_before_a_dull_day(self):
        evening = NOON.replace(hour=20)
        d, _ = decide_heating(inp(grid=300, battery=800, pv=0, sun_left=0, tomorrow=3000, at=evening),
                              HeatingConfig(), HeatState())
        self.assertEqual((d.mode, d.target), (Mode.NORMAL, 22.0))

    def test_not_in_summer(self):
        d, _ = run([inp(i, outside=18) for i in range(20)])
        self.assertEqual(d.mode, Mode.NORMAL)

    def test_bath_blocks_preheating(self):
        d, _ = run([inp(i, dhw="bath") for i in range(20)])
        self.assertNotEqual(d.mode, Mode.BOOST)


class HeatingTraceTests(unittest.TestCase):
    def test_every_path_ends_in_one_outcome(self):
        for steps in ([inp(i) for i in range(11)], [inp(0, outside=20)],
                      [inp(0, grid=0, battery=-2000, soc=40, free=1000, need=6000)],
                      [inp(0, grid=300, pv=0, sun_left=0, tomorrow=15000)]):
            d, _ = run(steps)
            self.assertEqual(sum(r["node"].startswith("h_out_") for r in d.trace), 1, d.trace)

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            HeatingConfig(room_min=23).validate()
        with self.assertRaises(ValueError):
            HeatingConfig(step_k=2).validate()


class HeatingControllerTests(unittest.TestCase):
    def test_writes_fine_targets_and_restores(self):
        regs = FakeRegisters(holding={1001: 220})            # register 1002 = Raumtemp. Tag
        ctl = HeatingController(ModbusDevice(SPECS["isg"], "fake", client=regs),
                                WriteLimits(min_write_interval_min=10, max_writes_per_day=30))
        cfg = HeatingConfig(apply=True)
        for i in range(11):
            ctl.step(inp(i), cfg, regs.holding[1001] / 10)
        self.assertEqual(regs.writes, [(1001, 218)])          # 21.8 °C: 0.1-K resolution
        entry = ctl.step(inp(30), HeatingConfig(apply=False), regs.holding[1001] / 10)
        self.assertIn("wiederhergestellt", entry["action"])
        self.assertEqual(regs.holding[1001], 220)


if __name__ == "__main__":
    unittest.main()

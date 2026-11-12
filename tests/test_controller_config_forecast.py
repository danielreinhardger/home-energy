import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homeenergy import config as config_mod
from homeenergy.controller import DhwController, WriteLimits
from homeenergy.device import ModbusDevice
from homeenergy.devices import SPECS
from homeenergy.forecast import Calibration, ForecastConfig, PvArray, fetch_hourly, summarize
from homeenergy.hotwater import DhwConfig, DhwInputs
from homeenergy.situation import Situation

from fakes import FakeRegisters

TZ = timezone(timedelta(hours=2))
NOON = datetime(2026, 6, 1, 12, 0, tzinfo=TZ)
ADDR = 1011  # WW Soll Tag, register 1012


def inp(now, grid=-2500.0, dhw=40.0):
    return DhwInputs(Situation(now, 4000, grid, 0, 100, 100, 10240, dhw_temp=dhw))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.regs = FakeRegisters(holding={ADDR: 420})
        self.ctl = DhwController(ModbusDevice(SPECS["isg"], "fake", client=self.regs),
                                 WriteLimits(min_write_interval_min=10, max_writes_per_day=2))

    def current(self):
        return self.regs.holding[ADDR] / 10

    def test_dry_run_never_writes(self):
        for i in range(10):
            self.ctl.step(inp(NOON + timedelta(minutes=i)), DhwConfig(apply=False), self.current())
        self.assertEqual(self.regs.writes, [])
        self.assertEqual(self.ctl.last["mode"], "reheat")
        self.assertTrue(self.ctl.last["dry_run"])

    def test_writes_reheat_then_rate_limits(self):
        cfg = DhwConfig(apply=True)
        for i in range(7):
            self.ctl.step(inp(NOON + timedelta(minutes=i)), cfg, self.current())
        self.assertEqual(self.regs.writes, [(ADDR, 460)])          # daily need: 46 °C with PV
        # 4 min later the comfort temperature is changed: the 10 min write gap holds it back
        entry = self.ctl.step(inp(NOON + timedelta(minutes=9), grid=300), DhwConfig(apply=True, comfort_temp=47),
                              self.current())
        self.assertIn("wartet", entry["action"])
        self.assertEqual(len(self.regs.writes), 1)

    def test_daily_limit(self):
        # no surplus (grid import): only the comfort target changes
        for minute, comfort in ((0, 47), (11, 48), (22, 49)):
            entry = self.ctl.step(inp(NOON + timedelta(minutes=minute), grid=300),
                                  DhwConfig(apply=True, comfort_temp=comfort, pv_only=False), self.current())
        self.assertIn("Tageslimit", entry["action"])
        self.assertEqual(len(self.regs.writes), 2)

    def test_restores_comfort_when_switched_off(self):
        self.ctl.step(inp(NOON, grid=300), DhwConfig(apply=True, comfort_temp=48, pv_only=False), self.current())
        self.assertEqual(self.current(), 48.0)
        entry = self.ctl.step(inp(NOON + timedelta(minutes=30), grid=300), DhwConfig(apply=False), self.current())
        self.assertIn("wiederhergestellt", entry["action"])
        self.assertEqual(self.current(), 46.0)
        self.ctl.step(inp(NOON + timedelta(minutes=45), grid=300), DhwConfig(apply=False), self.current())
        self.assertEqual(len(self.regs.writes), 2)  # only once

    def test_test_mode_copy_never_restores_from_saved_state(self):
        # A second instance started in test mode with a saved "restore pending" flag must not write.
        from homeenergy.controller import ControllerState
        ctl = DhwController(ModbusDevice(SPECS["isg"], "fake", client=self.regs), WriteLimits(),
                            state=ControllerState(needs_restore=True))
        self.regs.holding[ADDR] = 420
        entry = ctl.step(inp(NOON, grid=300), DhwConfig(apply=False), 42.0)
        self.assertEqual(self.regs.writes, [])
        self.assertIn("Testbetrieb", entry["action"])


class ConfigTests(unittest.TestCase):
    def test_roundtrip_and_section_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = config_mod.update_section(config_mod.AppConfig(), "dhw", {"comfort_temp": 47.5, "apply": True})
            cfg = config_mod.update_section(cfg, "forecast", {
                "enabled": True, "latitude": 51.3, "longitude": 9.5,
                "arrays": [{"kwp": 6, "tilt": 30, "azimuth": -90}, {"kwp": 5, "tilt": 30, "azimuth": 90}]})
            config_mod.save(cfg, path)
            back = config_mod.load(path)
            self.assertEqual(back.dhw.comfort_temp, 47.5)
            self.assertTrue(back.dhw.apply)
            self.assertEqual(back.forecast.arrays[1], PvArray(5, 30, 90))

    def test_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            config_mod.update_section(config_mod.AppConfig(), "dhw", {"boost_temp": 80})
        with self.assertRaises(ValueError):
            config_mod.update_section(config_mod.AppConfig(), "dhw", {"min_temp": 50})   # above comfort
        with self.assertRaises(ValueError):
            config_mod.update_section(config_mod.AppConfig(), "dhw", {"nope": 1})


class ForecastTests(unittest.TestCase):
    def fake_api(self, url):
        # 2 days in UTC; GTI 500 W/m² for hours ending 10:00..15:00 UTC (6 h), else 0
        times, gti = [], []
        for d in (1, 2):
            for h in range(24):
                times.append(f"2026-06-0{d}T{h:02d}:00")
                gti.append(500.0 if 10 <= h <= 15 else 0.0)
        return {"hourly": {"time": times, "global_tilted_irradiance": gti}}

    def test_fetch_and_summarize(self):
        cfg = ForecastConfig(enabled=True, latitude=51.3, longitude=9.5,
                             arrays=[PvArray(kwp=10)], performance_ratio=0.8)
        hours = fetch_hourly(cfg, fetch=self.fake_api)
        # each sunny hour: 10 kWp * 500 * 0.8 = 4000 Wh; 6 hours/day
        s = summarize(hours, NOON, calibration=0.5)
        self.assertAlmostEqual(s.today_wh, 12000)            # 24 kWh * 0.5
        self.assertAlmostEqual(s.tomorrow_wh, 12000)
        # NOON is 12:00 local = 10:00 UTC; sunny hours starting 10..14 UTC remain (5 h)
        self.assertAlmostEqual(s.remaining_today_wh, 10000)
        self.assertAlmostEqual(s.sun_hours_left_today, 5)

    def test_calibration_is_the_ratio_of_sums(self):
        c = Calibration(factor=0.73, days=0)
        log = {"2026-06-01": {"ahead_wh": 20000, "actual_wh": 16000},
               "2026-06-02": {"ahead_wh": 2000, "actual_wh": 3000}}
        self.assertFalse(c.refit(log))                                # 2 days: keep the old factor
        self.assertAlmostEqual(c.factor, 0.73)
        log["2026-06-03"] = {"ahead_wh": 10000, "actual_wh": 9000}
        log["2026-06-04"] = {"ahead_wh": 500, "actual_wh": 1500}      # dull day: left out
        log["2026-06-05"] = {"same_wh": 9000, "actual_wh": 8000}      # no day-ahead forecast: left out
        self.assertTrue(c.refit(log))
        self.assertAlmostEqual(c.factor, 28000 / 32000)              # sums, not a mean of ratios
        self.assertEqual(c.days, 3)

    def test_old_calibration_state_still_loads(self):
        c = Calibration.from_json({"factor": 0.73, "days": 3, "last_day": "2026-11-10", "alpha": 0.3})
        self.assertAlmostEqual(c.factor, 0.73)
        self.assertEqual(c.days, 0)                                    # not fitted by the new method yet
        self.assertEqual(Calibration.from_json({"factor": 0.8, "days": 5}).days, 5)

    def test_forecaster_logs_ahead_same_and_actual_per_local_day(self):
        import threading
        from homeenergy import forecaster as fc_mod
        from homeenergy.forecast import HourlyPv
        tz = timezone(timedelta(hours=1))
        now = [datetime(2026, 11, 11, 22, 0, tzinfo=tz)]
        start = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)   # = 11 Nov 00:00 local
        hours = [HourlyPv(start + timedelta(hours=i), 100.0) for i in range(72)]
        f = fc_mod.Forecaster(lambda: ForecastConfig(enabled=True, latitude=50, longitude=8),
                              lambda: now[0], threading.RLock(), {}, lambda: None)
        orig = fc_mod.fetch_hourly, fc_mod.fetch_weather
        fc_mod.fetch_hourly, fc_mod.fetch_weather = (lambda cfg: hours), (lambda cfg: None)
        try:
            f.refresh()
        finally:
            fc_mod.fetch_hourly, fc_mod.fetch_weather = orig
        self.assertEqual(f.log["2026-11-11"], {"same_wh": 2400.0})
        self.assertEqual(f.log["2026-11-12"], {"ahead_wh": 2400.0})
        self.assertNotIn("2026-11-10", f.log)                       # past and incomplete days untouched
        f.track_yield(3.0)
        now[0] = datetime(2026, 11, 12, 0, 1, tzinfo=tz)
        f.track_yield(0.0)
        self.assertEqual(f.log["2026-11-11"]["actual_wh"], 3000.0)
        self.assertEqual(f.accuracy()["recent"][0], {"day": "2026-11-11", "ahead_kwh": None,
                                                     "same_kwh": 2.4, "actual_kwh": 3.0})

if __name__ == "__main__":
    unittest.main()

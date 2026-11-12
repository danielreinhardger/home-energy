import unittest

from homeenergy.cop import CopModel, calibrate, datasheet_cop

ISG = {"heat_heating_total": 25153, "power_heating_total": 5488, "heat_dhw_total": 10978, "power_dhw_total": 4184}


class CopTests(unittest.TestCase):
    def test_datasheet_points_and_interpolation(self):
        self.assertAlmostEqual(datasheet_cop(-7), 2.61)
        self.assertAlmostEqual(datasheet_cop(2), 3.74)
        self.assertAlmostEqual(datasheet_cop(7), 4.74)
        self.assertAlmostEqual(datasheet_cop(4.5), (3.74 + 4.74) / 2)
        self.assertGreater(datasheet_cop(-15), 1.79)          # clamped, never absurd
        self.assertLessEqual(datasheet_cop(30), 6.0)
        self.assertAlmostEqual(datasheet_cop(9), 4.74 + 0.1 * 2)      # half slope above the last point

    def test_calibrated_to_the_counters(self):
        m = calibrate(ISG)
        # lifetime 4.58 vs datasheet 4.14 at 4 °C -> scale ~1.11
        self.assertAlmostEqual(m.heating_scale, 4.583 / datasheet_cop(4), places=2)
        self.assertAlmostEqual(m.heating(4), 4.58, places=1)
        self.assertLess(m.heating(-7), m.heating(7))
        self.assertAlmostEqual(m.dhw_cop, 2.62, places=2)

    def test_measured_heating_days_take_over(self):
        # three days like 9 Nov: COP ~3.66 at ~9.5 °C -> well below the datasheet (~5.0)
        days = [(9.5, 14.9, 4.07), (8.0, 16.0, 4.5), (6.0, 18.0, 5.2)]
        m = calibrate(ISG, heating_days=days)
        self.assertLess(m.heating_scale, 0.9)
        self.assertIn("gemessen", m.source)
        self.assertAlmostEqual(m.heating(9.5), 3.66, delta=0.35)
        self.assertEqual(calibrate(ISG, heating_days=days[:2]).heating_scale, calibrate(ISG).heating_scale)  # too few

    def test_recent_dhw_preferred_with_enough_data(self):
        m = calibrate(ISG, {"heat_dhw_kwh": 30, "power_dhw_kwh": 15})
        self.assertAlmostEqual(m.dhw_cop, 2.0)
        m = calibrate(ISG, {"heat_dhw_kwh": 6, "power_dhw_kwh": 3})          # too little
        self.assertAlmostEqual(m.dhw_cop, 2.62, places=2)

    def test_day_gain_from_forecast_temperatures(self):
        m = CopModel()
        self.assertGreater(m.day_gain(0, 8), 0.2)         # 0 °C night, 8 °C midday: clearly better by day
        self.assertLessEqual(m.day_gain(-10, 15), 0.25)   # capped
        self.assertEqual(m.day_gain(5, 5.5), 0.0)         # flat day: no advantage
        self.assertIsNone(m.day_gain(None, 8))

    def test_hot_water_cop_drops_when_hotter(self):
        m = CopModel(dhw_cop=2.6)
        self.assertAlmostEqual(m.dhw(46), 2.6)
        self.assertAlmostEqual(m.dhw(50), 2.6 * 0.88)


if __name__ == "__main__":
    unittest.main()

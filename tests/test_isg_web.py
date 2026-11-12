import unittest

from homeenergy.devices.isg_web import IsgWebDevice, PowerEstimator, live_cop, parse_page, parse_value

PAGE = """
<tr><td class="key">VERDAMPFERTEMP.</td><td class="value">34,5 &deg;C</td></tr>
<tr><td class="key">P HEIZUNG TAG</td><td class="value">0,897 kWh</td></tr>
<tr><td class="key">P WW TAG</td><td class="value">3,906 kWh</td></tr>
<tr><td class="key">WM HEIZEN SUMME</td><td class="value">25,153 MWh</td></tr>
"""


FACHMANN = """
<tr><td class="key">MOTORLEISTUNG</td><td class="value">0,85 kW</td></tr>
<tr><td class="key">VERDICHTERDREHZAHL</td><td class="value">42 Hz</td></tr>
<tr><td class="key round-leftbottom">HEIZ/K&Uuml;HL-LEISTUNG GEMESSEN</td><td class="value round-rightbottom">3,40 kW</td></tr>
"""


class IsgWebTests(unittest.TestCase):
    def test_parse(self):
        rows = parse_page(PAGE)
        self.assertEqual(rows["VERDAMPFERTEMP."], "34,5 °C")
        self.assertAlmostEqual(parse_value("20,03 bar"), 20.03)
        self.assertAlmostEqual(parse_value("25,153 MWh"), 25153.0)
        self.assertAlmostEqual(parse_value("0,897 kWh"), 0.897)
        self.assertEqual(parse_value("0 Hz"), 0)
        self.assertIsNone(parse_value(""))

    def test_device_values(self):
        d = IsgWebDevice("fake", fetch=lambda page: PAGE if page == "1,1" else FACHMANN)
        v = d.read_values()
        self.assertAlmostEqual(v["evaporator_temp"], 34.5)
        self.assertAlmostEqual(v["power_dhw_day"], 3.906)
        self.assertIsNone(v["oil_sump_temp"])          # not on this page
        self.assertAlmostEqual(v["compressor_kw"], 0.85)
        self.assertEqual(v["compressor_hz"], 42)
        self.assertAlmostEqual(v["heat_output_kw"], 3.40)
        self.assertAlmostEqual(v["cop_live"], 4.0)          # 3.40 kW heat ÷ 0.85 kW motor

    def test_live_cop_only_while_running_and_plausible(self):
        self.assertIsNone(live_cop(0.0, 0.0))               # idle
        self.assertIsNone(live_cop(2.0, 0.1))               # barely running (start-up)
        self.assertIsNone(live_cop(None, 0.8))
        self.assertIsNone(live_cop(0.5, 0.8))               # below 1: defrost or mismatched samples
        self.assertIsNone(live_cop(12.0, 0.8))              # above 10: same
        self.assertAlmostEqual(live_cop(2.62, 1.0), 2.62)

    def test_power_from_counters(self):
        p = PowerEstimator(window_s=300)
        self.assertIsNone(p.add(0, 4.000))
        self.assertIsNone(p.add(60, 4.020))              # under 2 min: not yet
        self.assertAlmostEqual(p.add(180, 4.060), 1200.0)  # 60 Wh in 3 min = 1.2 kW
        self.assertIsNone(p.add(240, 0.010))             # midnight reset starts over


if __name__ == "__main__":
    unittest.main()

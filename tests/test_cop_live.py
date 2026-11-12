"""Live COP: which mode a reading belongs to, and the fitted day/night ratio."""
import unittest

from homeenergy.cop import CopModel, fit_live
from homeenergy.devices.isg_web import IsgWebDevice
from homeenergy.store import Store

T0 = 1_794_000_000


def page(p_heating_kwh: float, p_dhw_kwh: float) -> str:
    return (f'<td class="key">P HEIZUNG TAG</td><td class="value">{p_heating_kwh:.3f} kWh</td>'
            f'<td class="key">P WW TAG</td><td class="value">{p_dhw_kwh:.3f} kWh</td>').replace(".", ",")


class ModeTests(unittest.TestCase):
    def test_mode_from_the_counter_that_grew(self):
        pages = iter([page(1.000, 2.000), page(1.015, 2.000), page(1.015, 2.020), page(1.016, 2.020)])
        d = IsgWebDevice("fake", fetch=lambda p: next(pages) if p == "1,1" else "")
        modes = []
        for _ in range(4):
            d._cache = None                               # skip the 60-s cache
            modes.append(d.read_values()["hp_mode"])
        self.assertEqual(modes, [None, "Heizen", "Warmwasser", None])   # last: 1 Wh = idle


class FitTests(unittest.TestCase):
    def test_too_little_data(self):
        self.assertIsNone(fit_live([(5.0, 4.0)] * 100))                          # under 2 h
        self.assertIsNone(fit_live([(5.0 + i % 3, 4.0) for i in range(300)]))   # span under 4 K

    def test_line_and_ratio(self):
        pts = [(t, 3.0 + 0.1 * t) for t in [0.0, 2.0, 4.0, 6.0, 8.0] for _ in range(40)]
        fit = fit_live(pts)
        self.assertAlmostEqual(fit.per_k, 0.1, places=3)
        self.assertAlmostEqual(fit.ratio(8.0, 0.0), 3.8 / 3.0, places=3)
        self.assertIsNone(fit.ratio(15.0, 0.0))                                   # outside the measured range

    def test_slope_never_negative(self):
        pts = [(t, 5.0 - 0.2 * t) for t in [0.0, 5.0, 10.0] for _ in range(50)]
        self.assertEqual(fit_live(pts).per_k, 0.0)

    def test_day_gain_prefers_measurement_inside_its_range(self):
        pts = [(t, 3.0 + 0.1 * t) for t in [0.0, 2.0, 4.0, 6.0, 8.0] for _ in range(40)]
        datasheet = CopModel()
        measured = CopModel(live=fit_live(pts))
        self.assertAlmostEqual(measured.day_gain(1.0, 9.0), (3.0 + 0.8) / (3.0 + 0.2) - 1, places=3)
        self.assertTrue(measured.day_gain_measured(1.0, 9.0))
        self.assertFalse(measured.day_gain_measured(-8.0, 0.0))                   # colder than measured
        self.assertAlmostEqual(measured.day_gain(-8.0, 0.0), datasheet.day_gain(-8.0, 0.0))


class StorePairsTests(unittest.TestCase):
    def test_pairs_only_where_both_are_set(self):
        s = Store(":memory:")
        s.add_samples([(T0, {"outside_temp": 5.0, "cop_heating_live": 4.2}),
                       (T0 + 60, {"outside_temp": 5.1, "cop_heating_live": None}),
                       (T0 + 120, {"outside_temp": 5.2, "cop_heating_live": 4.4})])
        self.assertEqual(s.pairs("outside_temp", "cop_heating_live", T0 - 1), [(5.0, 4.2), (5.2, 4.4)])
        self.assertEqual(s.pairs("outside_temp", "no_such_column", T0 - 1), [])


if __name__ == "__main__":
    unittest.main()

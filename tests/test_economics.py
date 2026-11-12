import unittest

from homeenergy.economics import EconomyConfig, merit_order, money_today


class EconomicsTests(unittest.TestCase):
    def test_merit_order(self):
        order = merit_order(EconomyConfig(), 4.6, 2.6, 46, 50)
        keys = [u.key for u in order]
        self.assertEqual(keys[0], "house")              # direct use always first
        self.assertEqual(keys[-1], "export")            # feeding in is the last resort
        # shifting needed heat into PV hours beats charging the battery
        self.assertLess(keys.index("heating"), keys.index("battery"))
        self.assertLess(keys.index("dhw_daily"), keys.index("battery"))
        # hot water beyond the need is worth less than the battery, but still more than feed-in
        extra = next(u for u in order if u.key == "dhw_extra")
        self.assertTrue(0.092 < extra.value < 0.315)
        self.assertAlmostEqual(next(u for u in order if u.key == "battery").value, 0.315)

    def test_hotter_reserve_is_worth_less(self):
        v50 = next(u.value for u in merit_order(EconomyConfig(), 4.6, 2.6, 46, 50) if u.key == "dhw_extra")
        v55 = next(u.value for u in merit_order(EconomyConfig(), 4.6, 2.6, 46, 55) if u.key == "dhw_extra")
        self.assertGreater(v50, v55)

    def test_money_today(self):
        m = money_today(4.52, 3.37, EconomyConfig())
        self.assertAlmostEqual(m["cost_eur"], 1.582)
        self.assertAlmostEqual(m["earn_eur"], 0.31004)
        self.assertAlmostEqual(m["net_eur"], 0.31004 - 1.582)


if __name__ == "__main__":
    unittest.main()

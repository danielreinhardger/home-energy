import unittest

from homeenergy.economics import EconomyConfig
from homeenergy.simulate import SCENARIOS, compare, run


class SimulationTests(unittest.TestCase):
    """The strategy must beat the old fixed targets on winter days without hurting comfort."""

    @classmethod
    def setUpClass(cls):
        cls.rows = {r["code"]: r for r in compare()}

    def test_winter_days_save_money_and_night_heating(self):
        for s in SCENARIOS:
            if s.outside_c >= 15:
                continue
            r = self.rows[s.name]
            self.assertGreaterEqual(r["saving_eur"], 0.0, r)                    # never worse in winter
            self.assertLess(r["hp_night_kwh"][1], r["hp_night_kwh"][0], r)      # less heating at night
            if s.pv_peak_kw >= 3:
                self.assertGreater(r["saving_eur"], 0.3, r)                     # sunny winter days pay clearly

    def test_colder_sunny_day_saves_more(self):
        # lower COP at night and more heat to move into the day: PV heating pays most when it's cold
        sunny = [s for s in SCENARIOS if s.outside_c < 15 and s.pv_peak_kw >= 3]
        coldest, mildest = min(sunny, key=lambda s: s.outside_c), max(sunny, key=lambda s: s.outside_c)
        self.assertGreater(self.rows[coldest.name]["saving_eur"], self.rows[mildest.name]["saving_eur"])

    def test_summer_is_not_worse(self):
        summer = next(s for s in SCENARIOS if s.outside_c >= 15)
        self.assertGreaterEqual(self.rows[summer.name]["saving_eur"], -0.05)

    def test_comfort_band_is_kept(self):
        for s in SCENARIOS:
            lo, hi = self.rows[s.name]["room_range"]
            self.assertGreaterEqual(lo, 19.7, s.name)       # room_min 20 − hysteresis
            self.assertLessEqual(hi, 24.3, s.name)          # room_max 24 + hysteresis

    def test_cheaper_feed_in_makes_storage_more_attractive(self):
        s = SCENARIOS[0]
        low = EconomyConfig(price_feed=0.05)
        self.assertGreater(run(s, False, eco=low).money(low) - run(s, True, eco=low).money(low), 0)


if __name__ == "__main__":
    unittest.main()

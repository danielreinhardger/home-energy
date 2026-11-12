import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.forecast import ForecastSummary, Weather, next_phase, phase_at

TZ = timezone(timedelta(hours=1))
DAY = datetime(2026, 11, 8, tzinfo=TZ)


class PhaseTests(unittest.TestCase):
    def test_phase_at(self):
        self.assertEqual(phase_at(DAY.replace(hour=17, minute=40))[0], "afternoon")
        self.assertEqual(phase_at(DAY.replace(hour=18))[0], "evening")
        self.assertEqual(phase_at(DAY.replace(hour=23))[0], "night")
        self.assertEqual(phase_at(DAY.replace(hour=3))[0], "night")

    def test_next_phase_skips_the_midnight_split(self):
        name, start, _ = next_phase(DAY.replace(hour=23, minute=10))
        self.assertEqual((name, start), ("morning", DAY.replace(hour=6) + timedelta(days=1)))
        name, start, _ = next_phase(DAY.replace(hour=17, minute=40))
        self.assertEqual((name, start.hour), ("evening", 18))

    def test_today_card_shows_now_and_next_with_night_flag(self):
        now = DAY.replace(hour=17, minute=40)
        utc = lambda dt: dt.astimezone(timezone.utc)
        weather = Weather(
            hourly_codes={utc(DAY.replace(hour=17)): 3, utc(DAY.replace(hour=20)): 0},
            daily=[],
            hourly_is_day={utc(DAY.replace(hour=17)): False, utc(DAY.replace(hour=20)): False})
        s = ForecastSummary(now, 1.0, 0, 0, 0, 0, [], weather, now)
        today = s.to_json()["days"][0]
        self.assertEqual([(p["label"], p["sub"], p["code"], p["night"]) for p in today["parts"]],
                         [("Jetzt", "Nachmittag", 3, True), ("Ab 18 Uhr", "Abend", 0, True)])
        tomorrow = s.to_json()["days"][1]
        self.assertEqual([p["label"] for p in tomorrow["parts"]], ["Vormittag", "Mittag", "Nachmittag"])


if __name__ == "__main__":
    unittest.main()

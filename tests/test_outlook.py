"""The 3-day outlook: one entry per day from tomorrow, weather + calibrated PV per local day."""
import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.forecast import HourlyPv, fetch_weather, outlook, ForecastConfig

CET = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=CET)


def fake_weather_json():
    start = datetime(2026, 11, 12, 1, 0)                  # hour *ending* times, UTC, 4 days
    times = [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(96)]
    days = ["2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"]
    return {"hourly": {"time": times, "weather_code": [3] * 96, "is_day": [1 if 7 <= (i % 24) <= 16 else 0 for i in range(96)],
                       "temperature_2m": [5.0 + (i % 24) / 4 for i in range(96)]},
            "daily": {"time": days, "weather_code": [3, 61, 1, 0], "temperature_2m_max": [9, 8, 11, 12],
                      "temperature_2m_min": [2, 3, 1, 0], "sunshine_duration": [3600, 0, 18000, 25200],
                      "precipitation_sum": [0, 6.4, 0.2, 0], "precipitation_probability_max": [10, 90, 20, 0],
                      "wind_speed_10m_max": [12, 31.6, 9, 5]}}


class OutlookTests(unittest.TestCase):
    def setUp(self):
        cfg = ForecastConfig(enabled=True, latitude=50.0, longitude=8.0)
        self.weather = fetch_weather(cfg, fetch=lambda url: fake_weather_json())
        # 1 kWh every daylight hour, 4 UTC days (local days 13 and 14 complete, 15 complete too)
        base = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
        self.hours = [HourlyPv(base + timedelta(hours=i), 1000.0 if 7 <= (i % 24) <= 15 else 0.0) for i in range(96)]

    def test_three_days_from_tomorrow(self):
        days = outlook(self.hours, self.weather, NOW, calibration=0.5)
        self.assertEqual([d["label"] for d in days], ["Morgen", "Übermorgen", "Sonntag"])
        tomorrow = days[0]
        self.assertEqual((tomorrow["code"], tomorrow["precip_mm"], tomorrow["precip_prob"], tomorrow["wind_max"]),
                         (61, 6.4, 90, 32))
        self.assertEqual(tomorrow["pv_kwh"], 4.5)                       # 9 h × 1 kWh × calibration 0.5
        self.assertEqual([p["hour"] for p in tomorrow["parts"]], [6, 9, 12, 15, 18, 21])
        self.assertIsNotNone(tomorrow["parts"][2]["temp"])

    def test_incomplete_day_has_no_pv_total(self):
        days = outlook(self.hours[:72], self.weather, NOW)               # forecast ends early on the 15th
        self.assertIsNone(days[2]["pv_kwh"])

    def test_without_weather(self):
        days = outlook(self.hours, None, NOW)
        self.assertIsNone(days[0]["code"])
        self.assertEqual(len(days), 3)


if __name__ == "__main__":
    unittest.main()

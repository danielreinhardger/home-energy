import tempfile
import unittest
from datetime import date
from pathlib import Path

from homeenergy.finance import DayRecord, Ledger
from homeenergy.store import Store


class LedgerTests(unittest.TestCase):
    def test_running_totals_only_grow_and_persist(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "finance.sqlite"
            led = Ledger(Store(path))
            day = date(2026, 11, 8)
            led.update(day, {"import_kwh": 4.0, "export_kwh": 3.0, "pv_kwh": 16, "house_kwh": 14, "car_kwh": 3.6}, 0.35, 0.092)
            led.update(day, {"import_kwh": 4.5, "export_kwh": 2.0}, 0.35, 0.092)   # stale export reading ignored
            led.save()
            rec = Ledger(Store(path)).days["2026-11-08"]
            self.assertEqual((rec.import_kwh, rec.export_kwh), (4.5, 3.0))
            self.assertAlmostEqual(rec.cost_eur, 1.575)
            self.assertAlmostEqual(rec.net_eur, 3.0 * 0.092 - 1.575)
            self.assertAlmostEqual(rec.without_pv_eur, 17.6 * 0.35)
            self.assertAlmostEqual(rec.benefit_eur, 17.6 * 0.35 + 3.0 * 0.092 - 1.575)

    def test_prices_are_kept_per_day(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            led = Ledger(Store(Path(d) / "f.sqlite"))
            led.update(date(2026, 11, 7), {"import_kwh": 10}, 0.35, 0.092)
            led.update(date(2026, 11, 8), {"import_kwh": 10}, 0.30, 0.092)        # new tariff from today
            self.assertAlmostEqual(led.days["2026-11-07"].cost_eur, 3.5)
            self.assertAlmostEqual(led.days["2026-11-08"].cost_eur, 3.0)

    def test_month_rows_sum_days(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            led = Ledger(Store(Path(d) / "f.sqlite"))
            for day, imp in ((date(2026, 10, 31), 5), (date(2026, 11, 1), 4), (date(2026, 11, 2), 6)):
                led.update(day, {"import_kwh": imp, "export_kwh": 1}, 0.35, 0.092)
            months = {m["month"]: m for m in led.month_rows()}
            self.assertEqual(months["2026-11"]["days"], 2)
            self.assertAlmostEqual(months["2026-11"]["import_kwh"], 10)
            self.assertAlmostEqual(months["2026-11"]["cost_eur"], 3.5)
            self.assertEqual(led.day_rows()[0]["day"], "2026-11-02")             # newest first

    def test_backup_heater_is_kept_per_day_and_month(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            led = Ledger(Store(Path(d) / "f.sqlite"))
            led.update(date(2026, 11, 20), {"import_kwh": 9, "backup_kwh": 2.0}, 0.35, 0.092)
            led.update(date(2026, 11, 20), {"backup_kwh": 3.0}, 0.35, 0.092)
            led.update(date(2026, 11, 21), {"backup_kwh": 1.0}, 0.35, 0.092)
            self.assertEqual(led.days["2026-11-20"].backup_kwh, 3.0)
            self.assertEqual(led.day_rows()[1]["backup_kwh"], 3.0)
            self.assertAlmostEqual(led.month_rows()[0]["backup_kwh"], 4.0)
            self.assertAlmostEqual(led.days["2026-11-20"].cost_eur, 9 * 0.35)       # already inside the import

    def test_device_resetting_late_after_midnight(self):
        # the ISG still reports yesterday's 3.906 kWh after our midnight and resets ~01:00
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "f.sqlite"
            led = Ledger(Store(path))
            led.update(date(2026, 11, 8), {"hp_power_dhw_kwh": 3.906}, 0.35, 0.092, hour=23)
            led.update(date(2026, 11, 9), {"hp_power_dhw_kwh": 3.906}, 0.35, 0.092, hour=0)   # carried over
            led.save()
            led = Ledger(Store(path))                                                               # survives a restart
            led.update(date(2026, 11, 9), {"hp_power_dhw_kwh": 0.004}, 0.35, 0.092, hour=1)  # device reset
            led.update(date(2026, 11, 9), {"hp_power_dhw_kwh": 2.772}, 0.35, 0.092, hour=19)
            self.assertAlmostEqual(led.days["2026-11-09"].hp_power_dhw_kwh, 2.772)
            self.assertAlmostEqual(led.days["2026-11-08"].hp_power_dhw_kwh, 3.906)
            # a drop later in the day is a stale reading and is ignored
            led.update(date(2026, 11, 9), {"hp_power_dhw_kwh": 1.0}, 0.35, 0.092, hour=20)
            self.assertAlmostEqual(led.days["2026-11-09"].hp_power_dhw_kwh, 2.772)

    def test_cop_only_for_real_heating(self):
        self.assertIsNone(DayRecord("d", hp_heat_heating_kwh=0.065, hp_power_heating_kwh=0.897).cop_heating)
        self.assertAlmostEqual(DayRecord("d", hp_heat_heating_kwh=14.8, hp_power_heating_kwh=4.062).cop_heating, 3.64)

    def test_outside_average_and_heating_days(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            led = Ledger(Store(Path(d) / "f.sqlite"))
            day = date(2026, 11, 9)
            for temp in (8, 10, 12):
                led.add_outside(day, temp)
            led.update(day, {"hp_heat_heating_kwh": 14.9, "hp_power_heating_kwh": 4.07}, 0.35, 0.092)
            self.assertEqual(led.days["2026-11-09"].outside_avg, 10.0)
            self.assertEqual(led.heating_days(14, today="2026-11-10"), [(10.0, 14.9, 4.07)])
            self.assertEqual(led.heating_days(14, today="2026-11-09"), [])       # today is not finished
            self.assertNotIn("outside_sum", led.day_rows()[0])

    def test_record_json_has_money(self):
        j = DayRecord("2026-11-08", import_kwh=1, export_kwh=1).to_json()
        self.assertIn("benefit_eur", j)


if __name__ == "__main__":
    unittest.main()

"""Alerts: problems become alerts only after their delay; one push when they start and end."""
import unittest
from datetime import datetime, timedelta, timezone

from homeenergy.alerts import AlertConfig, Alerts, BackupWatch, detect, send_push

T0 = datetime(2026, 11, 11, 12, 0, tzinfo=timezone(timedelta(hours=1)))
OK = {"isg": ("ISG", True, None), "kostal": ("Kostal", True, None)}


class DetectTests(unittest.TestCase):
    def test_all_fine(self):
        self.assertEqual(detect(AlertConfig(), OK, 45.0, {"dhw": ("", "unverändert")}, None, 0.5), [])

    def test_each_problem(self):
        devices = {**OK, "kostal": ("Kostal", False, "timeout")}
        found = detect(AlertConfig(), devices, 30.0, {"heating": ("failed", "Fehler beim Schreiben: refused")}, "HTTP 500", 5)
        self.assertEqual([p.key for p in found], ["device:kostal", "write:heating", "dhw:cold", "forecast"])
        self.assertIn("timeout", found[0].text)
        self.assertEqual(found[0].delay_min, 10)

    def test_recent_forecast_hides_a_single_failed_fetch(self):
        self.assertEqual(detect(AlertConfig(), OK, None, {}, "HTTP 500", 1.0), [])


class AlertsTests(unittest.TestCase):
    def setUp(self):
        self.now = T0
        self.sent = []
        self.alerts = Alerts(lambda: self.now, push=lambda url, msg, **kw: self.sent.append(msg), background=False)
        self.cfg = AlertConfig(push_url="https://ntfy.example/topic")
        self.problem = detect(self.cfg, {"kostal": ("Kostal", False, "timeout")}, None, {}, None, None)

    def at(self, minutes, problems):
        self.now = T0 + timedelta(minutes=minutes)
        self.alerts.update(problems, self.cfg)

    def test_short_glitch_is_no_alert(self):
        self.at(0, self.problem)
        self.at(5, self.problem)
        self.at(6, [])
        self.assertEqual((self.alerts.active, self.sent), ({}, []))

    def test_alert_after_delay_then_over(self):
        self.at(0, self.problem)
        self.at(10, self.problem)
        self.assertEqual(list(self.alerts.active), ["device:kostal"])
        self.at(11, self.problem)                                   # no second push while it lasts
        self.at(20, [])
        self.assertEqual(self.alerts.active, {})
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[0].startswith("⚠ Kostal"))
        self.assertTrue(self.sent[1].startswith("✓ behoben: Kostal"))
        self.assertEqual([e["kind"] for e in self.alerts.history], ["over", "start"])

    def test_no_address_no_push(self):
        self.cfg = AlertConfig()
        self.at(0, self.problem)
        self.at(10, self.problem)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.alerts.to_json(self.cfg)["push"], False)

    def test_failed_push_is_reported_not_raised(self):
        def broken(*a, **k):
            raise OSError("no route")
        self.alerts._push = broken
        self.at(0, self.problem)
        self.at(10, self.problem)
        self.assertEqual(self.alerts.push_error, "no route")

    def test_push_format(self):
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b""

        def opener(req, timeout):
            seen.update(url=req.full_url, body=req.data.decode(), title=req.get_header("Title"), method=req.method)
            return Resp()
        send_push("https://ntfy.sh/x", "⚠ Test", tags="warning", opener=opener)
        self.assertEqual(seen, {"url": "https://ntfy.sh/x", "body": "⚠ Test", "title": "Smarthome 28", "method": "POST"})
        with self.assertRaises(ValueError):
            send_push("ntfy.sh/x", "x", opener=opener)



class BackupWatchTests(unittest.TestCase):
    T = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)

    def test_one_message_per_day_at_the_threshold(self):
        w = BackupWatch()
        self.assertIsNone(w.update(self.T, 690, 100, 1))                       # first reading = start of day
        self.assertIsNone(w.update(self.T + timedelta(hours=1), 690, 100, 1))
        msg = w.update(self.T + timedelta(hours=2), 692, 101, 1)
        self.assertIn("3 kWh", msg)
        self.assertIn("Heizen 2, Warmwasser 1", msg)
        self.assertIsNone(w.update(self.T + timedelta(hours=3), 695, 101, 1))    # once a day
        self.assertEqual(w.total, 6)
        self.assertIsNone(w.update(self.T + timedelta(days=1), 695, 101, 1))     # new day starts at 0
        self.assertEqual(w.total, 0)

    def test_restart_keeps_the_day_and_zero_turns_it_off(self):
        w = BackupWatch()
        w.update(self.T, 690, 100, 0)
        again = BackupWatch(w.to_json())
        self.assertIsNone(again.update(self.T + timedelta(hours=1), 700, 100, 0))   # 0 = never notify
        self.assertEqual(again.total, 10)                                          # counted from the saved start

    def test_missing_or_reset_counters(self):
        w = BackupWatch()
        self.assertIsNone(w.update(self.T, None, 100, 1))
        w.update(self.T, 690, 100, 1)
        self.assertIsNone(w.update(self.T + timedelta(hours=1), 5, 0, 1))        # replaced controller: no jump
        self.assertEqual(w.total, 0)

    def test_notice_lands_in_history_and_push(self):
        sent = []
        a = Alerts(lambda: self.T, push=lambda url, msg, **kw: sent.append(msg), background=False)
        a.notice(AlertConfig(push_url="https://ntfy.example/x"), "Heizstab lief heute: 3 kWh")
        self.assertEqual(a.history[0]["kind"], "notice")
        self.assertEqual(sent, ["Heizstab lief heute: 3 kWh"])


if __name__ == "__main__":
    unittest.main()

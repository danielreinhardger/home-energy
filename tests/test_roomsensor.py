"""The room sensor: readings pushed over HTTP, how long they count, and that the heating never
uses the heat pump's echo of its own room target."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from homeenergy import views
from homeenergy.roomsensor import RoomSensor

from test_runtime import make_service

T = datetime(2026, 11, 12, 7, 0, tzinfo=timezone.utc)


class RoomSensorTests(unittest.TestCase):
    def test_reading_and_age(self):
        r = RoomSensor()
        r.update(T, "21,4", "48")                                     # as it may come from a URL
        self.assertEqual(r.current(T + timedelta(minutes=179), 180).temp, 21.4)
        self.assertEqual(r.current(T, 180).humidity, 48.0)
        self.assertIsNone(r.current(T + timedelta(minutes=181), 180))  # too old: no room temperature

    def test_rejects_nonsense(self):
        r = RoomSensor()
        for bad in (None, "", "abc", "99", "-3"):
            with self.assertRaises(ValueError):
                r.update(T, bad)
        r.update(T, 21.0, 140)                                         # impossible humidity is dropped
        self.assertIsNone(r.last.humidity)

    def test_survives_a_restart(self):
        r = RoomSensor()
        r.update(T, 22.5, 40)
        again = RoomSensor(r.to_json())
        self.assertEqual(again.current(T + timedelta(minutes=5), 180).temp, 22.5)
        self.assertIsNone(RoomSensor({"temp": "x", "at": "nonsense"}).last)


class ServiceRoomTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.s = make_service(Path(self._tmp.name))
        self.s.poller.devices["isg"].client.input[0] = 220      # "Raum-Ist HK1" = 22.0 °C (the echo)
        self.s.poll_once()

    def tearDown(self):
        self._tmp.cleanup()

    def test_echo_is_never_the_room_temperature(self):
        self.assertIsNotNone(self.s.poller.latest("isg").get("room_hc1"))   # the ISG reports something …
        self.assertIsNone(self.s.room_temp())                              # … but without a sensor: none
        self.s.room_reading("21.3", "45")
        self.assertEqual(self.s.room_temp(), 21.3)
        self.s.control.run(self.s.poller, None, self.s.cop_model(), self.s.room_temp())
        self.assertEqual(self.s.control.last_situation.room_temp, 21.3)
        self.assertEqual(self.s.room_sample(), {"room_sensor": 21.3, "room_humidity": 45.0})

    def test_stiebel_room_unit_option(self):
        self.s.update_config("heating", {"room_from_isg": True})
        self.assertEqual(self.s.room_temp(), self.s.poller.latest("isg").get("room_hc1"))

    def test_view_says_what_is_missing(self):
        v = views.room(self.s)
        self.assertFalse(v["fresh"])
        self.assertIsNone(v["used"])
        self.s.room_reading(20.8)
        self.assertTrue(views.room(self.s)["fresh"])


if __name__ == "__main__":
    unittest.main()

"""Hot water power limit: the rule, the write limits, and the ISG page write with read-back."""
import json
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone

from homeenergy.devices.isg_web import IsgWebDevice
from homeenergy.dhwpower import DhwPowerController, PowerState, power_target
from homeenergy.hotwater import DhwConfig, DhwMode as Mode

T = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
LIVE = DhwConfig(apply=True)


class FakeWeb:
    def __init__(self, value=80.0, fail=False):
        self.value, self.fail, self.writes = value, fail, []

    def write_setting(self, key, value):
        if self.fail:
            raise IOError("ISG hat nicht gespeichert")
        self.writes.append(value)
        self.value = value
        return value


class RuleTests(unittest.TestCase):
    def test_follows_the_surplus_only_while_pv_heats(self):
        c = DhwConfig()
        self.assertEqual(power_target(c, Mode.REHEAT, 80, 900, 0)[0], 100)      # exporting: more
        self.assertEqual(power_target(c, Mode.REHEAT, 100, 0, 450)[0], 50)      # drawing: less
        self.assertEqual(power_target(c, Mode.HYGIENE, 50, 100, 100)[0], 50)    # in between: keep
        self.assertEqual(power_target(c, Mode.SOLAR_WAIT, 100, 3000, 0)[0], 80) # no PV run: normal
        self.assertEqual(power_target(c, Mode.BATH, 80, 0, 900)[0], 100)        # bath: fast

    def test_config_order_is_checked(self):
        with self.assertRaises(ValueError):
            DhwConfig(power_low=90, power_normal=80).validate()


class ControllerTests(unittest.TestCase):
    def test_writes_with_limits(self):
        ctl, web = DhwPowerController(), FakeWeb()
        e = ctl.step(T, LIVE, Mode.REHEAT, 900, 0, 80.0, web)
        self.assertEqual(e["action"], "geschrieben: 100 %")
        e = ctl.step(T + timedelta(minutes=10), LIVE, Mode.REHEAT, 0, 500, 100.0, web)
        self.assertIn("wartet", e["action"])                                    # 30 min between changes
        e = ctl.step(T + timedelta(minutes=31), LIVE, Mode.REHEAT, 0, 500, 100.0, web)
        self.assertEqual(web.writes, [100, 50])
        e = ctl.step(T + timedelta(minutes=40), LIVE, Mode.REHEAT, 0, 500, 50.0, web)
        self.assertEqual(e["action"], "unverändert")

    def test_daily_limit_and_test_mode(self):
        st = PowerState(80.0, None, T.date().isoformat(), 8)
        e = DhwPowerController(st).step(T, LIVE, Mode.REHEAT, 900, 0, 80.0, FakeWeb())
        self.assertIn("Tageslimit", e["action"])
        e = DhwPowerController().step(T, DhwConfig(apply=False), Mode.REHEAT, 900, 0, 80.0, FakeWeb())
        self.assertIn("Testbetrieb", e["action"])

    def test_failure_is_reported_not_raised(self):
        e = DhwPowerController().step(T, LIVE, Mode.REHEAT, 900, 0, 80.0, FakeWeb(fail=True))
        self.assertTrue(e["action"].startswith("Fehler beim Schreiben"))

    def test_state_round_trip(self):
        st = PowerState(100.0, T, "2026-11-12", 3)
        self.assertEqual(PowerState.from_json(st.to_json()), st)


class PageWriteTests(unittest.TestCase):
    def make(self, answer='{"success":true,"message":"ok"}', stick=True):
        store = {"420": "80", "421": "80"}
        posts = []

        def fetch(page):
            return "".join(f"jsvalues['{k}']['val']='{v}';" for k, v in store.items()) if page == "4,1,1" else ""

        def post(path, body):
            data = json.loads(urllib.parse.parse_qs(body.decode())["data"][0])
            posts.append((path, data))
            if stick:
                for item in data:
                    store[item["name"][3:]] = item["value"]
            return answer

        d = IsgWebDevice("fake", fetch=fetch)
        d._post = post
        return d, posts

    def test_writes_both_fields_and_reads_back(self):
        d, posts = self.make()
        self.assertEqual(d.read_settings(), {"dhw_power": 80})
        self.assertEqual(d.write_setting("dhw_power", 100), 100)
        self.assertEqual(posts, [("save.php", [{"name": "val420", "value": "100"}, {"name": "val421", "value": "100"}])])

    def test_refuses_and_detects_failures(self):
        d, _ = self.make()
        with self.assertRaises(PermissionError):
            d.write_setting("dhw_day", 50)
        with self.assertRaises(ValueError):
            d.write_setting("dhw_power", 20)
        d, _ = self.make(answer='{"success":false}')
        with self.assertRaises(IOError):
            d.write_setting("dhw_power", 100)


if __name__ == "__main__":
    unittest.main()

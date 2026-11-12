import json
import time
import unittest

from homeenergy.devices.openwb import OpenWbDevice
from homeenergy.flow import build_flow, fallback_hierarchy, kostal_powers

HIERARCHY = [{"id": 0, "type": "counter", "children": [
    {"id": 2, "type": "inverter", "children": [{"id": 4, "type": "bat", "children": []}]},
    {"id": 6, "type": "cp", "children": []}]}]
NAMES = {0: "Kostal Zähler", 2: "Kostal WR", 4: "Kostal Speicher", 6: "openWB2"}


def edge(flow, a, b):
    return next(e for e in flow["edges"] if {e["from"], e["to"]} == {a, b})


class FlowTests(unittest.TestCase):
    def test_sunny_charging_car_and_battery(self):
        # PV 6 kW, battery charging 2 kW, car 3 kW, export 0.5 kW -> house 0.5 kW
        flow = build_flow(HIERARCHY, NAMES, {0: -500, 2: -6000, 4: 2000, 6: 3000}, 500)
        self.assertEqual(edge(flow, "pv2", "c2"), {"from": "pv2", "to": "c2", "w": 6000})
        self.assertEqual(edge(flow, "c2", "c4"), {"from": "c2", "to": "c4", "w": 2000})
        self.assertEqual(edge(flow, "c2", "c0"), {"from": "c2", "to": "c0", "w": 4000})   # AC out
        self.assertEqual(edge(flow, "c0", "c6"), {"from": "c0", "to": "c6", "w": 3000})
        self.assertEqual(edge(flow, "utility", "c0"), {"from": "c0", "to": "utility", "w": 500})  # export
        self.assertEqual(edge(flow, "c0", "house"), {"from": "c0", "to": "house", "w": 500})

    def test_balance_at_the_meter(self):
        flow = build_flow(HIERARCHY, NAMES, {0: 49.9, 2: -57.93, 4: -436.07, 6: 0}, 543.9)
        into = sum(e["w"] for e in flow["edges"] if e["to"] == "c0")
        out = sum(e["w"] for e in flow["edges"] if e["from"] == "c0")
        self.assertAlmostEqual(into, out, places=6)

    def test_night_battery_discharge(self):
        flow = build_flow(HIERARCHY, NAMES, {0: 0, 2: 0, 4: -800, 6: 0}, 800)
        self.assertEqual(edge(flow, "c4", "c2")["from"], "c4")
        bat = next(n for n in flow["nodes"] if n["id"] == "c4")
        self.assertEqual((bat["role"], bat["role_text"]), ("source", "entlädt"))

    def test_balance_sources_equal_sinks(self):
        flow = build_flow(HIERARCHY, NAMES, {0: -500, 2: -6000, 4: 2000, 6: 3000}, 500)
        bal = flow["balance"]
        self.assertEqual([s["label"] for s in bal["sources"]], ["PV"])
        self.assertEqual(sorted(s["label"] for s in bal["sinks"]),
                         ["Auto", "Batterie laden", "Einspeisung", "Haus"])
        self.assertAlmostEqual(sum(s["w"] for s in bal["sources"]), sum(s["w"] for s in bal["sinks"]))
        roles = {n["id"]: n["role"] for n in flow["nodes"]}
        self.assertEqual((roles["pv2"], roles["c4"], roles["c6"], roles["utility"]),
                         ("source", "sink", "sink", "sink"))

    def test_missing_power_gives_unmetered_edge(self):
        flow = build_flow(HIERARCHY, NAMES, {0: 100, 2: None, 4: 0, 6: 0}, 100)
        self.assertIsNone(edge(flow, "c2", "c0")["w"])
        self.assertIsNone(edge(flow, "house", "heatpump")["w"])

    def test_fallback_from_kostal(self):
        h, names = fallback_hierarchy()
        powers, home = kostal_powers({"grid_power": -300, "pv_power": 2000, "battery_power": -1000,
                                      "home_from_pv": 700, "home_from_battery": 0, "home_from_grid": 0})
        flow = build_flow(h, names, powers, home)
        self.assertEqual(edge(flow, "c2", "c4"), {"from": "c2", "to": "c4", "w": 1000})   # charging
        self.assertEqual(edge(flow, "c2", "c0")["w"], 1000)
        self.assertEqual(edge(flow, "utility", "c0")["from"], "c0")


class FakeMqtt:
    def __init__(self, values):
        self.values = values
        self.connected = True
        self.last_error = None
        self.last_message = time.time()

    def start(self):
        pass

    def stop(self):
        pass

    def get(self, topic):
        return self.values.get(topic)

    def snapshot(self):
        return dict(self.values)


class OpenWbDeviceTests(unittest.TestCase):
    def device(self, **extra):
        values = {
            "openWB/counter/get/hierarchy": json.dumps(HIERARCHY),
            "openWB/chargepoint/6/get/power": "3680.5",
            "openWB/chargepoint/6/get/plug_state": "true",
            "openWB/chargepoint/6/get/daily_imported": "3603.03",
            "openWB/chargepoint/6/set/charge_template": json.dumps({"chargemode": {"selected": "pv_charging"}}),
            "openWB/chargepoint/6/get/currents": "[5.1, 5.3, 5.0]",
            "openWB/chargepoint/6/config": json.dumps({"id": 6, "name": "openWB2"}),
            "openWB/system/device/1/component/2/config": json.dumps({"id": 2, "name": "Kostal WR"}),
            "openWB/pv/2/get/power": "-6000",
            "openWB/bat/4/get/power": "2000",
            "openWB/counter/0/get/power": "-500",
            **extra,
        }
        return OpenWbDevice("fake", subscriber=FakeMqtt(values))

    def test_values_follow_the_hierarchy_ids(self):
        v = self.device().read_values()
        self.assertEqual(v["cp_power"], 3680.5)
        self.assertEqual(v["cp_plugged"], 1)
        self.assertEqual(v["cp_mode"], "pv_charging")               # a code; the text comes from the catalog
        self.assertEqual(v["cp_current"], 5.3)
        self.assertAlmostEqual(v["cp_daily"], 3.60303)
        self.assertEqual(v["grid_power"], -500)

    def test_component_powers_and_names(self):
        d = self.device()
        self.assertEqual(d.component_powers(), {0: -500.0, 2: -6000.0, 4: 2000.0, 6: 3680.5})
        self.assertEqual(d.component_names(), {2: "Kostal WR", 6: "openWB2"})

    def test_disconnected_raises(self):
        d = self.device()
        d.mqtt.connected = False
        with self.assertRaises(ConnectionError):
            d.read_values()


if __name__ == "__main__":
    unittest.main()


class HeatPumpNodeTests(unittest.TestCase):
    def test_metered_heat_pump(self):
        flow = build_flow(HIERARCHY, NAMES, {0: 0, 2: -3000, 4: 0, 6: 0}, 3000, isg={"hp_power_w": 1200.0})
        hp = next(n for n in flow["nodes"] if n["id"] == "heatpump")
        self.assertEqual((hp["power_w"], hp["role_text"]), (1200.0, "verbraucht"))
        self.assertEqual(edge(flow, "house", "heatpump")["w"], 1200.0)

    def test_unmetered_heat_pump(self):
        flow = build_flow(HIERARCHY, NAMES, {0: 0, 2: -3000, 4: 0, 6: 0}, 3000)
        hp = next(n for n in flow["nodes"] if n["id"] == "heatpump")
        self.assertEqual((hp["power_w"], hp["role_text"]), (None, "Teil des Hauses"))

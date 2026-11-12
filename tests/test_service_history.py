import tempfile
import unittest
from pathlib import Path

from homeenergy.device import ModbusDevice
from homeenergy.devices import SPECS
from homeenergy import views
from homeenergy.service import Service

from fakes import FakeRegisters, german_config


def make_service(tmp: Path) -> Service:
    devices = {"isg": ModbusDevice(SPECS["isg"], "fake", client=FakeRegisters()),
               "kostal": ModbusDevice(SPECS["kostal"], "fake", client=FakeRegisters())}
    german_config(tmp / "config.json")                       # the texts these tests check are German
    return Service(tmp / "config.json", tmp / "data", devices=devices)


class HistoryPersistenceTests(unittest.TestCase):
    def test_history_survives_a_restart(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            tmp = Path(d)
            s1 = make_service(tmp)
            s1.books.record_history(views.energy(s1.poller))
            s1.store.close()
            s2 = make_service(tmp)
            rows = views.history(s2)
            self.assertEqual(len(rows), 1)
            self.assertIn("t", rows[0])
            self.assertIn("pv_w", rows[0])
            s2.store.close()

    def test_decisions_survive_a_restart(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            tmp = Path(d)
            s1 = make_service(tmp)
            s1.store.add_decision("dhw", {"time": "2026-11-09T20:38:00+01:00", "mode": "save", "target": 42.0})
            s1.store.close()
            s2 = make_service(tmp)
            self.assertEqual(s2.control.dhw.recent[0]["mode"], "save")
            s2.store.close()


if __name__ == "__main__":
    unittest.main()

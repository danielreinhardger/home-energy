import struct
import unittest

from homeenergy.device import ModbusDevice
from homeenergy.devices import SPECS
from homeenergy.modbus import ModbusExceptionResponse, ModbusTcpClient
from homeenergy.registers import f32_cdab, kwh_mwh_pair, plan_blocks, s16

from fakes import FakeModbusServer, FakeRegisters


def cdab(value: float) -> tuple[int, int]:
    hi, lo = struct.unpack(">HH", struct.pack(">f", value))
    return lo, hi


class DecoderTests(unittest.TestCase):
    def test_s16_scale_negative_and_na(self):
        self.assertEqual(s16(0.1)([235]), 23.5)
        self.assertEqual(s16(0.1)([0xFFF6]), -1.0)
        self.assertIsNone(s16(0.1)([0x8000]))

    def test_kwh_mwh_pair(self):
        self.assertEqual(kwh_mwh_pair([153, 25]), 25153)   # WM Heizen Summe 25,153 MWh
        self.assertIsNone(kwh_mwh_pair([0x8000, 1]))

    def test_f32_cdab_matches_live_frequency(self):
        # Live Kostal bytes e0c0 4247 = 49.97 Hz
        self.assertAlmostEqual(f32_cdab()([0xE0C0, 0x4247]), 49.97, places=2)


class BlockPlanTests(unittest.TestCase):
    def test_merges_small_gaps_and_splits_tables(self):
        blocks = plan_blocks([("input", 1, 1), ("input", 3, 2), ("input", 30, 1), ("holding", 5, 1)], max_gap=4)
        self.assertEqual([(b.table, b.start, b.count) for b in blocks],
                         [("input", 1, 4), ("input", 30, 1), ("holding", 5, 1)])

    def test_zero_gap_keeps_adjacent_together_only(self):
        blocks = plan_blocks([("holding", 100, 2), ("holding", 102, 2), ("holding", 106, 2)], max_gap=0)
        self.assertEqual([(b.start, b.count) for b in blocks], [(100, 4), (106, 2)])


class ClientTests(unittest.TestCase):
    def test_read_write_and_exception_over_tcp(self):
        regs = FakeRegisters({0: 235, 1: 236}, {1011: 460})
        with FakeModbusServer(regs) as srv:
            c = ModbusTcpClient("127.0.0.1", srv.port)
            self.assertEqual(c.read_input(0, 2), [235, 236])
            c.write_single(1011, 480)
            self.assertEqual(c.read_holding(1011, 1), [480])
            with self.assertRaises(ModbusExceptionResponse) as ctx:
                c.read_input(500, 1)
            self.assertEqual(ctx.exception.code, 2)
            self.assertEqual(c.read_input(0, 1), [235])   # connection still usable
            c.close()


class DeviceTests(unittest.TestCase):
    def test_isg_setting_validation_and_readback(self):
        regs = FakeRegisters(holding={1011: 460})        # register 1012 = address 1011
        dev = ModbusDevice(SPECS["isg"], "fake", client=regs)
        self.assertEqual(dev.write_setting("dhw_day", 48.5), 48.5)
        self.assertEqual(regs.writes, [(1011, 485)])
        with self.assertRaises(ValueError):
            dev.write_setting("dhw_day", 70)
        with self.assertRaises(ValueError):
            dev.write_setting("dhw_day", 48.3)            # not on the 0.5 grid
        with self.assertRaises(PermissionError):
            dev.write_setting("room_day_hc2", 20)         # unverified register stays read-only

    def test_mode_options(self):
        dev = ModbusDevice(SPECS["isg"], "fake", client=FakeRegisters(holding={1000: 11}))
        dev.write_setting("mode", 1)
        with self.assertRaises(ValueError):
            dev.write_setting("mode", 2)

    def test_kostal_decodes_floats(self):
        regs = FakeRegisters(holding={})
        for reg, val in ((1066, 1557.5), (252, -370.0), (210, 97.0)):
            regs.holding[reg], regs.holding[reg + 1] = cdab(val)
        regs.holding[582] = 0x10000 - 623                    # charging 623 W
        values = ModbusDevice(SPECS["kostal"], "fake", client=regs).read_values()
        self.assertAlmostEqual(values["pv_power"], 1557.5)
        self.assertAlmostEqual(values["grid_power"], -370.0)
        self.assertEqual(values["battery_power"], -623)


if __name__ == "__main__":
    unittest.main()

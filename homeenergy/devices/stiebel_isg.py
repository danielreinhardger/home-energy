"""Stiebel Eltron ISG web with an LWZ ventilation heat pump (old "LWZ" Modbus map).

This ISG numbers registers from 1 (not 501 like the current WPM map). Every
point below was compared with the ISG web pages on 2026-11-08; see
docs/register-verification.md for how each setting was verified.
"""
from ..registers import DeviceSpec, Point, Setting, bits, kwh_mwh_pair, s16, u16

T, P = "°C", "bar"

POINTS = (
    # Anlage (?s=1,0)
    Point("room_hc1", 1, s16(0.1), T, 'heating'),
    Point("room_hc1_target", 2, s16(0.1), T, 'heating'),
    Point("humidity_hc1", 3, s16(0.1), "%", 'heating'),
    Point("room_hc2", 4, s16(0.1), T, 'heating'),
    Point("room_hc2_target", 5, s16(0.1), T, 'heating'),
    Point("outside_temp", 7, s16(0.1), T, 'heating'),
    Point("hc1_actual", 8, s16(0.1), T, 'heating'),
    Point("hc1_target", 9, s16(0.1), T, 'heating'),
    Point("hc2_actual", 10, s16(0.1), T, 'heating'),
    Point("hc2_target", 11, s16(0.1), T, 'heating'),
    Point("flow_temp", 12, s16(0.1), T, 'heating'),
    Point("return_temp", 13, s16(0.1), T, 'heating'),
    Point("pressure", 14, s16(0.1), P, 'heating'),
    Point("dhw_temp", 16, s16(0.1), T, 'dhw'),
    # Follows the active hot water target (it moved to 46.0 some minutes after
    # WW SOLL TAG was written).
    Point("dhw_target", 17, s16(0.1), T, 'dhw'),
    Point("supply_fan_hz", 18, s16(), "Hz", 'ventilation', digits=0),
    Point("supply_flow", 19, s16(), "m³/h", 'ventilation', digits=0),
    Point("extract_fan_hz", 20, s16(), "Hz", 'ventilation', digits=0),
    Point("extract_flow", 21, s16(), "m³/h", 'ventilation', digits=0),
    Point("extract_humidity", 22, s16(), "%", 'ventilation', digits=0),
    Point("extract_temp", 23, s16(0.1), T, 'ventilation'),
    Point("extract_dew_point", 24, s16(0.1), T, 'ventilation'),
    # Wärmepumpe (?s=1,1)
    Point("hot_gas_temp", 28, s16(0.1), T, 'heat_pump'),
    Point("high_pressure", 29, s16(0.01), P, 'heat_pump', digits=2),
    Point("low_pressure", 30, s16(0.01), P, 'heat_pump', digits=2),
    # Status bits: meaning not mapped yet, exposed raw.
    Point("status_bits", 2001, bits, "", 'status', digits=0),
    Point("status_2002", 2002, u16(), "", 'status', digits=0),
    # Energy (?s=1,1). Day values are whole kWh; totals are kWh.
    Point("heat_heating_day", 3001, u16(), "kWh", 'energy', digits=0),
    Point("heat_heating_total", 3002, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("heat_dhw_day", 3004, u16(), "kWh", 'energy', digits=0),
    Point("heat_dhw_total", 3005, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("heat_backup_heating_total", 3007, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("heat_backup_dhw_total", 3009, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("heat_recovery_day", 3011, u16(), "kWh", 'energy', digits=0),
    Point("heat_recovery_total", 3012, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("power_heating_total", 3023, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("power_dhw_day", 3025, u16(), "kWh", 'energy', digits=0),
    Point("power_dhw_total", 3026, kwh_mwh_pair, "kWh", 'energy', words=2, digits=0),
    Point("hours_heating", 3028, u16(), "h", 'hours', digits=0),
    Point("hours_dhw", 3030, u16(), "h", 'hours', digits=0),
    Point("hours_backup_heating", 3031, u16(), "h", 'hours', digits=0),
    Point("hours_backup_dhw", 3032, u16(), "h", 'hours', digits=0),
)

MODES = (11, 1, 3, 4, 5, 14, 0)          # texts: dev.isg.set.mode.<code>

SETTINGS = (
    Setting("mode", 1001, 'mode', options=MODES, verified="value-match"),
    # HEIZEN (?s=4,0)
    # the ISG page accepts 0.1 K steps for room temperatures (change('val5', ..., '0.1'))
    Setting("room_day_hc1", 1002, 'heating', T, 0.1, 10, 30, 0.1, verified="value-match"),
    Setting("room_night_hc1", 1003, 'heating', T, 0.1, 10, 30, 0.1, verified="value-match"),
    Setting("hc_manual", 1004, 'heating', T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("room_day_hc2", 1005, 'heating', T, 0.1, 10, 30, 0.5),
    Setting("room_night_hc2", 1006, 'heating', T, 0.1, 10, 30, 0.5),
    # WARMWASSER (?s=4,1)
    Setting("dhw_day", 1012, 'dhw', T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("dhw_night", 1013, 'dhw', T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("dhw_manual", 1014, 'dhw', T, 0.1, 10, 65, 0.5, verified="value-match"),
    # LÜFTEN (?s=4,2)
    Setting("fan_day", 1018, 'ventilation', "", 1, 0, 3, 1, verified="write-test"),
    Setting("fan_night", 1019, 'ventilation', "", 1, 0, 3, 1, verified="write-test"),
    Setting("fan_party", 1020, 'ventilation', "", 1, 0, 3, 1, verified="value-match"),
    Setting("fan_manual", 1021, 'ventilation', "", 1, 0, 3, 1, verified="write-test"),
)

SPEC = DeviceSpec(
    key="isg",
    port=502,
    unit_id=1,
    register_base=1,
    points=POINTS,
    settings=SETTINGS,
    read_max_gap=8,
    page_only=("heating", "dhw", "ventilation", "energy", "programs"),
)

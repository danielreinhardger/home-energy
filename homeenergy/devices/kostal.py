"""KOSTAL PLENTICORE plus hybrid inverter (Modbus TCP, port 1502, unit 71).

Register numbers are the protocol addresses from the KOSTAL "MODBUS-TCP/SunSpec"
interface description (0-based). Floats are word-swapped (CDAB). Values were
cross-checked against each other on 2026-11-08 (PV strings vs DC total, battery
power vs DC/AC balance, capacity 10240 Wh = the 10.2 kWh battery).

Read-only on purpose: the inverter's settings (battery management, min SOC...)
need the installer login on its own web page, and the Modbus min/max SOC
registers only take effect with external battery control enabled.
"""
from ..registers import DeviceSpec, Point, ascii_string, f32_cdab, s16, u32_cdab

W, V, A = "W", "V", "A"


def f(key, reg, unit="", group="", scale=1.0, digits=1):
    return Point(key, reg, f32_cdab(scale), unit, group, words=2, table="holding", digits=digits)


# inverter status (register 56) → the text the page shows
STATES = (0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)   # texts: dev.kostal.state.<code>

POINTS = (
    Point("product", 768, ascii_string, "", 'info', words=32, table="holding"),
    Point("state", 56, u32_cdab, "", 'info', words=2, table="holding", digits=0, options=STATES),
    # PV
    f("pv_power", 1066, W, 'pv', digits=0),
    f("dc1_power", 260, W, 'pv', digits=0),
    f("dc1_voltage", 266, V, 'pv', digits=0),
    f("dc1_current", 258, A, 'pv', digits=2),
    f("dc2_power", 270, W, 'pv', digits=0),
    f("dc2_voltage", 276, V, 'pv', digits=0),
    f("dc2_current", 268, A, 'pv', digits=2),
    f("dc_total_power", 100, W, 'pv', digits=0),
    f("ac_power", 172, W, 'grid', digits=0),
    f("grid_frequency", 152, "Hz", 'grid', digits=2),
    # Grid meter: positive = import from grid, negative = feed-in
    f("grid_power", 252, W, 'grid', digits=0),
    # House
    f("home_from_pv", 116, W, 'house', digits=0),
    f("home_from_battery", 106, W, 'house', digits=0),
    f("home_from_grid", 108, W, 'house', digits=0),
    # Battery: power positive = discharging, negative = charging (KOSTAL reg 582)
    Point("battery_power", 582, s16(na=None), W, 'battery', table="holding", digits=0),
    f("battery_soc", 210, "%", 'battery', digits=0),
    f("battery_voltage", 216, V, 'battery', digits=0),
    f("battery_temp", 214, "°C", 'battery'),
    f("battery_cycles", 194, "", 'battery', digits=0),
    f("battery_capacity", 1068, "Wh", 'battery', digits=0),
    f("battery_min_soc", 1042, "%", 'battery', digits=0),
    f("battery_max_soc", 1044, "%", 'battery', digits=0),
    # Energy (Wh registers shown as kWh)
    f("yield_day", 322, "kWh", 'energy', 1e-3, 2),
    f("yield_month", 326, "kWh", 'energy', 1e-3, 0),
    f("yield_year", 324, "kWh", 'energy', 1e-3, 0),
    f("yield_total", 320, "kWh", 'energy', 1e-3, 0),
    f("home_total", 118, "kWh", 'energy', 1e-3, 0),
    f("home_total_pv", 114, "kWh", 'energy', 1e-3, 0),
    f("home_total_battery", 110, "kWh", 'energy', 1e-3, 0),
    f("home_total_grid", 112, "kWh", 'energy', 1e-3, 0),
    f("feed_in_total", 1064, "kWh", 'energy', 1e-3, 0),
    f("battery_charge_total", 1046, "kWh", 'energy', 1e-3, 0),
    f("battery_discharge_total", 1048, "kWh", 'energy', 1e-3, 0),
)

SPEC = DeviceSpec(
    key="kostal",
    port=1502,
    unit_id=71,
    register_base=0,
    points=POINTS,
    read_max_gap=0,
    page_only=("all",),
)

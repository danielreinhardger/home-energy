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


def f(key, label, reg, unit="", group="", scale=1.0, digits=1):
    return Point(key, label, reg, f32_cdab(scale), unit, group, words=2, table="holding", digits=digits)


# inverter status (register 56) → the text the page shows
STATES = {0: "Aus", 1: "Init", 2: "Isolationsmessung", 3: "Netzprüfung", 4: "Anlauf",
          6: "Einspeisen", 7: "Abgeregelt", 8: "Externe Abschaltung", 9: "Update",
          10: "Standby", 11: "Netzsynchronisation", 12: "Netzvorprüfung",
          13: "Netzabschaltung", 14: "Übertemperatur", 15: "Herunterfahren",
          16: "DC-Spannung ungültig", 17: "Notstrom"}

POINTS = (
    Point("product", "Produkt", 768, ascii_string, "", "Info", words=32, table="holding"),
    Point("state", "Wechselrichter-Status", 56, u32_cdab, "", "Info", words=2, table="holding", digits=0,
          options=STATES),
    # PV
    f("pv_power", "PV-Leistung gesamt", 1066, W, "PV", digits=0),
    f("dc1_power", "DC1 Leistung", 260, W, "PV", digits=0),
    f("dc1_voltage", "DC1 Spannung", 266, V, "PV", digits=0),
    f("dc1_current", "DC1 Strom", 258, A, "PV", digits=2),
    f("dc2_power", "DC2 Leistung", 270, W, "PV", digits=0),
    f("dc2_voltage", "DC2 Spannung", 276, V, "PV", digits=0),
    f("dc2_current", "DC2 Strom", 268, A, "PV", digits=2),
    f("dc_total_power", "DC-Leistung gesamt (inkl. Batterie)", 100, W, "PV", digits=0),
    f("ac_power", "AC-Wirkleistung", 172, W, "Netz", digits=0),
    f("grid_frequency", "Netzfrequenz", 152, "Hz", "Netz", digits=2),
    # Grid meter: positive = import from grid, negative = feed-in
    f("grid_power", "Netzbezug (+) / Einspeisung (−)", 252, W, "Netz", digits=0),
    # House
    f("home_from_pv", "Hausverbrauch aus PV", 116, W, "Haus", digits=0),
    f("home_from_battery", "Hausverbrauch aus Batterie", 106, W, "Haus", digits=0),
    f("home_from_grid", "Hausverbrauch aus Netz", 108, W, "Haus", digits=0),
    # Battery: power positive = discharging, negative = charging (KOSTAL reg 582)
    Point("battery_power", "Batterie Entladen (+) / Laden (−)", 582, s16(na=None), W, "Batterie",
          table="holding", digits=0),
    f("battery_soc", "Ladezustand", 210, "%", "Batterie", digits=0),
    f("battery_voltage", "Batteriespannung", 216, V, "Batterie", digits=0),
    f("battery_temp", "Batterietemperatur", 214, "°C", "Batterie"),
    f("battery_cycles", "Ladezyklen", 194, "", "Batterie", digits=0),
    f("battery_capacity", "Nutzbare Kapazität", 1068, "Wh", "Batterie", digits=0),
    f("battery_min_soc", "Min. SOC (externe Steuerung)", 1042, "%", "Batterie", digits=0),
    f("battery_max_soc", "Max. SOC (externe Steuerung)", 1044, "%", "Batterie", digits=0),
    # Energy (Wh registers shown as kWh)
    f("yield_day", "Ertrag heute", 322, "kWh", "Energie", 1e-3, 2),
    f("yield_month", "Ertrag Monat", 326, "kWh", "Energie", 1e-3, 0),
    f("yield_year", "Ertrag Jahr", 324, "kWh", "Energie", 1e-3, 0),
    f("yield_total", "Ertrag gesamt", 320, "kWh", "Energie", 1e-3, 0),
    f("home_total", "Hausverbrauch gesamt", 118, "kWh", "Energie", 1e-3, 0),
    f("home_total_pv", "Hausverbrauch aus PV gesamt", 114, "kWh", "Energie", 1e-3, 0),
    f("home_total_battery", "Hausverbrauch aus Batterie gesamt", 110, "kWh", "Energie", 1e-3, 0),
    f("home_total_grid", "Hausverbrauch aus Netz gesamt", 112, "kWh", "Energie", 1e-3, 0),
    f("feed_in_total", "Einspeisung gesamt", 1064, "kWh", "Energie", 1e-3, 0),
    f("battery_charge_total", "Batterie geladen gesamt (DC)", 1046, "kWh", "Energie", 1e-3, 0),
    f("battery_discharge_total", "Batterie entladen gesamt (DC)", 1048, "kWh", "Energie", 1e-3, 0),
)

SPEC = DeviceSpec(
    key="kostal",
    name="KOSTAL PLENTICORE plus (PV)",
    port=1502,
    unit_id=71,
    register_base=0,
    points=POINTS,
    read_max_gap=0,
    page_only=("Alle Wechselrichter-Einstellungen (Installateur-Login auf der Kostal-Webseite)",),
)

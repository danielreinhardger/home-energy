"""Stiebel Eltron ISG web with an LWZ ventilation heat pump (old "LWZ" Modbus map).

This ISG numbers registers from 1 (not 501 like the current WPM map). Every
point below was compared with the ISG web pages on 2026-11-08; see
docs/register-verification.md for how each setting was verified.
"""
from ..registers import DeviceSpec, Point, Setting, bits, kwh_mwh_pair, s16, u16

T, P = "°C", "bar"

POINTS = (
    # Anlage (?s=1,0)
    Point("room_hc1", "Raum-Ist HK1", 1, s16(0.1), T, "Heizen"),
    Point("room_hc1_target", "Raum-Soll HK1", 2, s16(0.1), T, "Heizen"),
    Point("humidity_hc1", "Raumfeuchte HK1", 3, s16(0.1), "%", "Heizen"),
    Point("room_hc2", "Raum-Ist HK2", 4, s16(0.1), T, "Heizen"),
    Point("room_hc2_target", "Raum-Soll HK2", 5, s16(0.1), T, "Heizen"),
    Point("outside_temp", "Außentemperatur", 7, s16(0.1), T, "Heizen"),
    Point("hc1_actual", "Istwert HK1", 8, s16(0.1), T, "Heizen"),
    Point("hc1_target", "Sollwert HK1", 9, s16(0.1), T, "Heizen"),
    Point("hc2_actual", "Istwert HK2", 10, s16(0.1), T, "Heizen"),
    Point("hc2_target", "Sollwert HK2", 11, s16(0.1), T, "Heizen"),
    Point("flow_temp", "Vorlauftemperatur", 12, s16(0.1), T, "Heizen"),
    Point("return_temp", "Rücklauftemperatur", 13, s16(0.1), T, "Heizen"),
    Point("pressure", "Druck Heizkreis", 14, s16(0.1), P, "Heizen"),
    Point("dhw_temp", "Warmwasser-Ist", 16, s16(0.1), T, "Warmwasser"),
    # Follows the active hot water target (it moved to 46.0 some minutes after
    # WW SOLL TAG was written).
    Point("dhw_target", "Warmwasser-Soll (aktiv)", 17, s16(0.1), T, "Warmwasser"),
    Point("supply_fan_hz", "Zuluft Lüfterdrehzahl", 18, s16(), "Hz", "Lüften", digits=0),
    Point("supply_flow", "Zuluft Soll-Volumenstrom", 19, s16(), "m³/h", "Lüften", digits=0),
    Point("extract_fan_hz", "Abluft Lüfterdrehzahl", 20, s16(), "Hz", "Lüften", digits=0),
    Point("extract_flow", "Abluft Soll-Volumenstrom", 21, s16(), "m³/h", "Lüften", digits=0),
    Point("extract_humidity", "Abluftfeuchte", 22, s16(), "%", "Lüften", digits=0),
    Point("extract_temp", "Ablufttemperatur", 23, s16(0.1), T, "Lüften"),
    Point("extract_dew_point", "Abluft-Taupunkt", 24, s16(0.1), T, "Lüften"),
    # Wärmepumpe (?s=1,1)
    Point("hot_gas_temp", "Heißgastemperatur", 28, s16(0.1), T, "Wärmepumpe"),
    Point("high_pressure", "Hochdruck", 29, s16(0.01), P, "Wärmepumpe", digits=2),
    Point("low_pressure", "Niederdruck", 30, s16(0.01), P, "Wärmepumpe", digits=2),
    # Status bits: meaning not mapped yet, exposed raw.
    Point("status_bits", "Statusbits (roh)", 2001, bits, "", "Status", digits=0),
    Point("status_2002", "Status 2002 (roh)", 2002, u16(), "", "Status", digits=0),
    # Energy (?s=1,1). Day values are whole kWh; totals are kWh.
    Point("heat_heating_day", "Wärme Heizen heute", 3001, u16(), "kWh", "Energie", digits=0),
    Point("heat_heating_total", "Wärme Heizen gesamt", 3002, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("heat_dhw_day", "Wärme Warmwasser heute", 3004, u16(), "kWh", "Energie", digits=0),
    Point("heat_dhw_total", "Wärme Warmwasser gesamt", 3005, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("heat_backup_heating_total", "Wärme Heizstab Heizen gesamt", 3007, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("heat_backup_dhw_total", "Wärme Heizstab WW gesamt", 3009, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("heat_recovery_day", "Wärmerückgewinnung heute", 3011, u16(), "kWh", "Energie", digits=0),
    Point("heat_recovery_total", "Wärmerückgewinnung gesamt", 3012, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("power_heating_total", "Strom Heizen gesamt", 3023, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("power_dhw_day", "Strom Warmwasser heute", 3025, u16(), "kWh", "Energie", digits=0),
    Point("power_dhw_total", "Strom Warmwasser gesamt", 3026, kwh_mwh_pair, "kWh", "Energie", words=2, digits=0),
    Point("hours_heating", "Verdichter Heizen", 3028, u16(), "h", "Betriebsstunden", digits=0),
    Point("hours_dhw", "Verdichter Warmwasser", 3030, u16(), "h", "Betriebsstunden", digits=0),
    Point("hours_backup_heating", "Heizstab Heizen", 3031, u16(), "h", "Betriebsstunden", digits=0),
    Point("hours_backup_dhw", "Heizstab Warmwasser", 3032, u16(), "h", "Betriebsstunden", digits=0),
)

MODES = {11: "Automatik", 1: "Bereitschaft", 3: "Tagbetrieb", 4: "Absenkbetrieb",
         5: "Warmwasser", 14: "Handbetrieb", 0: "Notbetrieb"}

SETTINGS = (
    Setting("mode", "Betriebsart", 1001, "Betriebsart", options=MODES, verified="value-match"),
    # HEIZEN (?s=4,0)
    # the ISG page accepts 0.1 K steps for room temperatures (change('val5', ..., '0.1'))
    Setting("room_day_hc1", "Raumtemp. Tag", 1002, "Heizen", T, 0.1, 10, 30, 0.1, verified="value-match"),
    Setting("room_night_hc1", "Raumtemp. Nacht", 1003, "Heizen", T, 0.1, 10, 30, 0.1, verified="value-match"),
    Setting("hc_manual", "Heizkreissoll Hand", 1004, "Heizen", T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("room_day_hc2", "Raumtemp. Tag HK2", 1005, "Heizen", T, 0.1, 10, 30, 0.5,
            note="Nicht auf der ISG-Seite; vermutlich HK2."),
    Setting("room_night_hc2", "Raumtemp. Nacht HK2", 1006, "Heizen", T, 0.1, 10, 30, 0.5,
            note="Nicht auf der ISG-Seite; vermutlich HK2."),
    # WARMWASSER (?s=4,1)
    Setting("dhw_day", "WW Soll Tag", 1012, "Warmwasser", T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("dhw_night", "WW Soll Nacht", 1013, "Warmwasser", T, 0.1, 10, 65, 0.5, verified="write-test"),
    Setting("dhw_manual", "WW Soll Handbetrieb", 1014, "Warmwasser", T, 0.1, 10, 65, 0.5, verified="value-match"),
    # LÜFTEN (?s=4,2)
    Setting("fan_day", "Stufe Tag", 1018, "Lüften", "", 1, 0, 3, 1, verified="write-test"),
    Setting("fan_night", "Stufe Nacht", 1019, "Lüften", "", 1, 0, 3, 1, verified="write-test"),
    Setting("fan_party", "Stufe Party", 1020, "Lüften", "", 1, 0, 3, 1, verified="value-match"),
    Setting("fan_manual", "Stufe Hand", 1021, "Lüften", "", 1, 0, 3, 1, verified="write-test"),
)

SPEC = DeviceSpec(
    key="isg",
    name="Stiebel Eltron ISG (Wärmepumpe)",
    port=502,
    unit_id=1,
    register_base=1,
    points=POINTS,
    settings=SETTINGS,
    read_max_gap=8,
    page_only=(
        "HEIZEN: Raumtemp. Bereitschaft",
        "WARMWASSER: WW Soll Bereitschaft",
        "LÜFTEN: Stufe Bereitschaft",
        "ENERGIEMANAGEMENT: Ein/Aus, obere Raum-/Puffer- und Warmwassertemperatur, Puffer",
        "PROGRAMME: Heiz-, Warmwasser-, Lüfter-, Party-, Ferienprogramm, Antilegionellen",
    ),
)

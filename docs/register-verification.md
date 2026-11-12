# Register verification

How each value in the device maps was confirmed (2026-11-08).

## Stiebel Eltron ISG (Modbus 502, old LWZ map, registers from 1)

**Input registers 1–30, 3001–3032:** every value was compared with the ISG pages
ANLAGE (`?s=1,0`) and WÄRMEPUMPE (`?s=1,1`) and matched. Register 17 follows the
active hot water target: it changed to 46.0 a few minutes after WW SOLL TAG was written.

Status bits (register 2001), partly mapped on 2026-11-08 against the status page:
bit 10 = HEIZKREISPUMPE (appeared alone with that page row); bits 1 and 4 = VERDICHTER and
WARMWASSERBEREITUNG (order unknown); bits 0, 6, 8 = SCHALTPROGRAMM AKTIV, SERVICE, FILTERWECHSEL
(order unknown); register 2002 = 1 while EVU-SPERRE shows.

Not mapped: 15 (flow rate?), 31–34 (no match with page values), the rest of the status
bits (exposed raw; the status page shows icons only, and in 15 min of logging the bits
didn't change), 5001–5002.

**Holding registers (settings):**

| Reg | Setting | Value seen | Page field | Status |
|---|---|---|---|---|
| 1001 | Betriebsart | 11 | start page `val39` = 11 (Automatik) | value-match → writable |
| 1002 | Raumtemp. Tag | 23.5 | HEIZEN 23,5 | value-match → writable |
| 1003 | Raumtemp. Nacht | 15.0 | HEIZEN 15,0 | value-match → writable |
| 1004 | Heizkreissoll Hand | 35.0 | HEIZEN `val54` moved 35,0→35,5 | **write-test** → writable |
| 1005/1006 | HK2 Tag/Nacht? | 19.0 | not on page (input 5 = 19.0) | read-only |
| 1007 | HK2 Hand? | 35.0 | write-test moved no page field | not exposed |
| 1008–1011 | ? | 24, 0, 30, 30 | not on page | not exposed |
| 1012 | WW Soll Tag | 50.1 → 46.0 | WARMWASSER, followed two writes | **write-test** → writable |
| 1013 | WW Soll Nacht | 10.0 | WARMWASSER `val161` moved 10,0→10,5 | **write-test** → writable |
| 1014 | WW Soll Handbetrieb | 40.0 | 40,0 | value-match → writable |
| 1015–1017 | ? | 200, 150, 150 | not on page | not exposed |
| 1018 | Lüften Stufe Tag | 2 | LÜFTEN `val82` moved 2→1 | **write-test** → writable |
| 1019 | Lüften Stufe Nacht | 0 | LÜFTEN `val83` moved 0→1 | **write-test** → writable |
| 1020 | Lüften Stufe Party | 3 | Party 3 | value-match → writable |
| 1021 | Lüften Stufe Hand | 2 | LÜFTEN `val188` moved 2→1 | **write-test** → writable |
| 4001 | ? (SG-Ready / Energiemanagement?) | 0 | ENERGIEMANAGEMENT AUS | not exposed |

**Write test (2026-11-08, approved by the user):** each register was nudged by one
step, the four settings pages were re-read to see which field moved, and the original
value was written back straight away. All registers and pages were confirmed back at
their original values afterwards. To check a new register the same way, nudge it, reload
the device page, restore it, then set `verified="write-test"` in
`homeenergy/devices/stiebel_isg.py`.

## KOSTAL PLENTICORE plus (Modbus 1502, unit 71)

Floats are CDAB (low word first): grid frequency read 49.97 Hz. Cross-checks:
DC1 + DC2 ≈ PV total (1066); PV − battery charge ≈ DC total (100); AC ≈ house + export;
register 582 negative while charging (matches DC balance); capacity 1068 = 10240 Wh.
The web UI needs a login, so values were not compared with it.

## openWB (MQTT 1883, software 2.2.1)

Read-only subscription; no login needed on the local broker. Checked 2026-11-08:

- `openWB/counter/get/hierarchy` = counter 0 → (inverter 2 → bat 4), cp 6. Component names
  from `openWB/system/device/1/component/*/config`: openWB reads the Kostal itself over
  Modbus (port 1502, unit 71).
- Balance: `home_consumption` 640.2 W = counter 0.2 W − pv (−115.7 W) − bat (−524.3 W) − cp 0 W,
  so openWB's home figure excludes the charge point, and its signs are counter + import,
  pv − generation, bat + charging.
- openWB's PV daily yield (22.97 kWh) is higher than the Kostal's own (16.5 kWh) on the same
  day, probably because openWB counts the hybrid inverter's AC output including battery
  discharge. The dashboard shows the Kostal value for PV yield.

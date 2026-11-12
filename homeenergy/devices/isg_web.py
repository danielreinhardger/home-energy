"""Values only the ISG web pages show (read-only HTTP: ?s=1,1 "WÄRMEPUMPE", ?s=2,3 "FACHMANN").

Modbus on this ISG lacks the refrigerant temperatures and gives the day energy
counters only in whole kWh. The page has them to the watt-hour, which lets the
service derive the heat pump's actual electric power (it has no meter of its own).
Read at most once a minute to keep the ISG light.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any

MIN_INTERVAL_S = 60
UNIT_FACTOR = {"MWh": 1000.0, "kWh": 1.0, "Wh": 0.001}


@dataclass(frozen=True)
class WebPoint:
    key: str
    label: str            # dashboard label
    page_label: str       # label on the ISG page
    unit: str = ""
    group: str = ""
    digits: int = 1


POINTS = (
    WebPoint("hp_power_w", "Wärmepumpe Stromaufnahme (Ø 5 min)", "", "W", "Wärmepumpe", 0),
    WebPoint("evaporator_temp", "Verdampfertemperatur", "VERDAMPFERTEMP.", "°C", "Wärmepumpe"),
    WebPoint("condenser_temp", "Verflüssigertemperatur", "VERFLÜSSIGERTEMP.", "°C", "Wärmepumpe"),
    WebPoint("oil_sump_temp", "Ölsumpftemperatur", "OELSUMPFTEMP", "°C", "Wärmepumpe"),
    WebPoint("exhaust_fan_hz", "Fortluft Lüfterdrehzahl", "FORTLUFT IST LÜFTERDREHZAHL", "Hz", "Lüften", 0),
    WebPoint("exhaust_flow_pct", "Fortluft Soll-Volumenstrom", "FORTLUFT SOLL VOLUMENSTROM", "%", "Lüften", 0),
    WebPoint("evaporator_dp", "Differenzdruck Verdampfer", "DIFF.-DRUCK VERDAMPF", "Pa", "Wärmepumpe", 0),
    WebPoint("heat_heating_day", "Wärme Heizen heute", "WM HEIZEN TAG", "kWh", "Energie heute", 3),
    WebPoint("heat_dhw_day", "Wärme Warmwasser heute", "WM WW TAG", "kWh", "Energie heute", 3),
    WebPoint("heat_recovery_day", "Wärmerückgewinnung heute", "WM WRG TAG", "kWh", "Energie heute", 3),
    WebPoint("power_heating_day", "Strom Heizen heute", "P HEIZUNG TAG", "kWh", "Energie heute", 3),
    WebPoint("power_dhw_day", "Strom Warmwasser heute", "P WW TAG", "kWh", "Energie heute", 3),
    # FACHMANN process values: the inverter compressor and its heat output, live (no power setpoint exists)
    WebPoint("compressor_kw", "Verdichter Motorleistung", "MOTORLEISTUNG", "kW", "Verdichter", 2),
    WebPoint("heat_output_kw", "Heizleistung gemessen", "HEIZ/KÜHL-LEISTUNG GEMESSEN", "kW", "Verdichter", 2),
    WebPoint("cop_live", "Leistungszahl jetzt (Wärme ÷ Verdichter)", "", "", "Verdichter", 1),
    WebPoint("hp_mode", "Betrieb gerade (aus den Zählern)", "", "", "Verdichter", 0),
    WebPoint("compressor_hz", "Verdichterdrehzahl", "VERDICHTERDREHZAHL", "Hz", "Verdichter", 0),
    WebPoint("compressor_demand_pct", "Vorgabe Verdichter", "VORGABE VERDICHTER", "%", "Verdichter", 0),
    WebPoint("heating_output_pct", "Heizleistung relativ", "HEIZLEISTUNG RELATIV", "%", "Verdichter", 0),
    WebPoint("compressor_current_a", "Motorstrom", "MOTORSTROM", "A", "Verdichter", 1),
    WebPoint("inverter_temp", "Invertertemperatur", "INVERTERTEMPERATUR", "°C", "Verdichter", 1),
    WebPoint("evaporator_out_temp", "Verdampferausgang", "VERDAMPFERAUSG. TEMP.", "°C", "Verdichter", 1),
)
PAGES = ("1,1", "2,3")


def parse_value(text: str) -> float | None:
    """'20,03 bar' -> 20.03; '25,153 MWh' -> 25153.0 (kWh); '1.234,5 kWh' -> 1234.5."""
    m = re.match(r"\s*(-?[\d.]*\d(?:,\d+)?)\s*(\S+)?", text or "")
    if not m:
        return None
    number = float(m.group(1).replace(".", "").replace(",", "."))
    return number * UNIT_FACTOR.get(m.group(2) or "", 1.0)


def parse_page(page: str) -> dict[str, str]:
    # the last row of each table has extra classes (class="key round-leftbottom")
    rows = re.findall(r'<td class="key[^"]*">(.*?)</td>\s*<td class="value[^"]*">(.*?)</td>', page, re.S)
    return {html.unescape(re.sub("<[^>]+>", "", k)).strip(): html.unescape(re.sub("<[^>]+>", "", v)).strip()
            for k, v in rows}


@dataclass(frozen=True)
class IsgWebSpec:
    key: str = "isg_web"
    name: str = "Stiebel Eltron ISG (Webseite)"
    points: tuple[WebPoint, ...] = POINTS
    settings: tuple = ()
    page_only: tuple = ()
    reads_settings: bool = True       # read_settings(): the hot water power limit (no Modbus settings)


def live_cop(heat_kw: float | None, motor_kw: float | None) -> float | None:
    """Heat output ÷ compressor motor power, both from the FACHMANN page, right now.
    Only while the compressor really runs (≥ 0.2 kW); implausible ratios (start-up, defrost,
    the two values sampled at slightly different moments) are dropped rather than shown.
    The motor power leaves out fans, pumps and controls, so this sits above the counter COP."""
    if heat_kw is None or motor_kw is None or motor_kw < 0.2 or heat_kw <= 0:
        return None
    cop = heat_kw / motor_kw
    return round(cop, 2) if 1.0 <= cop <= 10.0 else None


class PowerEstimator:
    """Heat pump electric power from the growth of its day counters (Ø over a few minutes)."""

    def __init__(self, window_s: float = 300):
        self.window_s = window_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, t: float, total_kwh: float | None) -> float | None:
        if total_kwh is None:
            return None
        if self.samples and total_kwh < self.samples[-1][1] - 1e-6:      # counters reset at midnight
            self.samples.clear()
        self.samples.append((t, total_kwh))
        while self.samples and t - self.samples[0][0] > self.window_s:
            self.samples.popleft()
        return self.watts()

    def watts(self) -> float | None:
        if len(self.samples) < 2:
            return None
        (t0, e0), (t1, e1) = self.samples[0], self.samples[-1]
        if t1 - t0 < 120:                                                  # need ≥ 2 min for 1 Wh steps
            return None
        return (e1 - e0) * 1000 * 3600 / (t1 - t0)


class IsgWebDevice:
    def __init__(self, host: str, fetch=None):
        self.spec = IsgWebSpec()
        self.host = host
        self._fetch = fetch or self._http
        self._cache: dict[str, Any] | None = None
        self._cache_t = 0.0
        self.power = PowerEstimator()
        self._last_counters: tuple | None = None

    def _http(self, page: str) -> str:
        req = urllib.request.Request(f"http://{self.host}/?s={page}", headers={"User-Agent": "home-energy/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8", "replace")

    def read_values(self) -> dict[str, Any]:
        now = time.time()
        if self._cache is not None and now - self._cache_t < MIN_INTERVAL_S:
            return self._cache
        rows = {}
        for page in PAGES:
            rows.update(parse_page(self._fetch(page)))
        if not rows:
            raise IOError("ISG-Webseite ohne Werte")
        values = {p.key: parse_value(rows[p.page_label]) if p.page_label and p.page_label in rows else None
                  for p in POINTS}
        p_h, p_w = values.get("power_heating_day"), values.get("power_dhw_day")
        total = None if p_h is None or p_w is None else p_h + p_w
        values["hp_power_w"] = self.power.add(now, total)
        values["cop_live"] = live_cop(values.get("heat_output_kw"), values.get("compressor_kw"))
        values["hp_mode"] = self._mode(p_h, p_w)
        self._cache, self._cache_t = values, now
        return values

    def _mode(self, p_heating: float | None, p_dhw: float | None) -> str | None:
        """Heizen or Warmwasser: whichever Wh day counter grew since the last read (None = idle/unknown)."""
        prev, self._last_counters = self._last_counters, (p_heating, p_dhw)
        if prev is None or None in prev or p_heating is None or p_dhw is None:
            return None
        dh, dw = p_heating - prev[0], p_dhw - prev[1]
        if max(dh, dw) < 0.002:                         # < 2 Wh: idle (or the midnight reset)
            return None
        return "Warmwasser" if dw > dh else "Heizen"

    # -- the one setting written through the page: hot water power limit (not in the Modbus map) --
    POWER_FIELDS = ("420", "421")             # WW-LEISTUNG SOMMER / WINTER on ?s=4,1,1, both kept equal

    def read_settings(self) -> dict:
        page = self._fetch("4,1,1")
        vals = [re.search(rf"jsvalues\['{f}'\]\['val'\]='([^']*)'", page) for f in self.POWER_FIELDS]
        if not all(vals):
            return {}
        nums = [parse_value(v.group(1)) for v in vals]
        return {"dhw_power": nums[1]} if None not in nums else {}          # winter value (both equal)

    def write_setting(self, key, value):
        """Write the hot water power limit like the page's "Speichern" button (POST save.php),
        then read it back – the page is undocumented, so only a read-back counts."""
        if key != "dhw_power":
            raise PermissionError(f"ISG-Webseite: {key} wird nicht geschrieben")
        pct = int(round(float(value)))
        if not 30 <= pct <= 100:
            raise ValueError(f"WW-Leistung {pct} % außerhalb 30…100 %")
        data = json.dumps([{"name": f"val{f}", "value": str(pct)} for f in self.POWER_FIELDS])
        answer = self._post("save.php", urllib.parse.urlencode({"data": data}).encode())
        if '"success":true' not in answer.replace(" ", ""):
            raise IOError(f"ISG hat nicht gespeichert: {answer[:120]}")
        for _ in range(5):
            if self.read_settings().get("dhw_power") == pct:
                return pct
            time.sleep(2)
        raise IOError(f"WW-Leistung {pct} % gespeichert, aber nicht zurückgelesen")

    def _post(self, path: str, body: bytes) -> str:
        req = urllib.request.Request(f"http://{self.host}/{path}", data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "home-energy/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", "replace")

    def close(self) -> None:
        pass

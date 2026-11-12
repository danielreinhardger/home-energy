"""Save every setting of the heat pump as the ISG web pages show it (read only, nothing is written).

What a replacement control board would need re-entered: room and hot water targets, heating
curves, electric backup, ventilation stages and airflows, energy management (SG Ready), the
time programs – plus the raw Modbus setting registers for comparison.

    python tools/isg_backup.py <host>        → backups/isg-settings-YYYY-MM-DD.json and .md
"""
from __future__ import annotations

import html
import json
import re
import socket
import struct
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit("usage: python tools/isg_backup.py <isg-address>")
HOST = sys.argv[1]
OUT = Path(__file__).resolve().parent.parent / "backups"

SETTINGS_PAGES = ["4,0", "4,1", "4,2", "4,3", "4,5", "3,3", "3,4", "3,5", "3,6", "3,7"]
PROGRAM_PAGES = ["3,0", "3,1", "3,2"]           # heating, hot water, fan: day sliders in 15-min units


def fetch(page: str) -> str:
    req = urllib.request.Request(f"http://{HOST}/?s={page}", headers={"User-Agent": "home-energy/backup"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8", "replace")
    time.sleep(0.3)                              # keep the ISG light
    return text


def clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def subpages(page: str, text: str) -> list[str]:
    found = re.findall(r'href="\?s=(' + re.escape(page) + r',\d+)"', text)
    return sorted(set(found), key=lambda p: [int(x) for x in p.split(",")])


def page_title(text: str) -> str:
    m = (re.search(r"""\$\("#subnavactivename"\)\.html\('([^']*)'\)""", text)
         or re.search(r'<div id="sub_nav"[^>]*><div class="left main[^"]*"[^>]*>(.*?)</div>', text, re.S))
    return clean(m.group(1)) if m else ""


def fields(text: str) -> list[dict]:
    """Each settings box: title, value, unit and the allowed range or options."""
    out = []
    for m in re.finditer(r'<div class="calibration[^"]*"(?: id="calval([^"]+)")?[^>]*>(.*?)(?=<div class="calibration|<div id ?=|</form>)',
                         text, re.S):
        fid, body = m.group(1) or "", m.group(2)
        title = clean((re.search(r"<h3[^>]*>(.*?)</h3>", body, re.S) or [None, ""])[1])
        unit = clean((re.search(r'<div class="values span-1[^"]*">(.*?)</div>', body, re.S) or [None, ""])[1])
        f = {"id": fid, "title": title, "unit": unit}
        radios = re.findall(r'<input id="radioval[^"]*" type="radio" alt="([^"]*)"[^>]*value="([^"]*)"([^>]*)>', body)
        if re.search(r'<select|<input class="date"', body):    # dates and times (holiday, party, start times)
            parts, dates, times = [], [], []
            for d, s in re.findall(r'<input class="date"[^>]*value="([^"]*)"|<select[^>]*>(.*?)</select>', body, re.S):
                if d:
                    dates.append(d)
                    if len(dates) == 3:
                        parts.append(f"{dates[0]}.{dates[1]}.20{dates[2]}"); dates = []
                else:
                    times.append((re.search(r'<option value="([^"]*)"\s*selected', s) or [None, "?"])[1])
                    if len(times) == 2:
                        parts.append(":".join(times)); times = []
            f["value"], f["unit"] = " ".join(parts), ""
        elif radios:
            f["options"] = [html.unescape(a) for a, _, _ in radios]
            checked = [html.unescape(a) for a, _, rest in radios if "checked" in rest]
            f["value"] = checked[0] if checked else None
        else:
            v = re.search(r"jsvalues\['" + re.escape(fid) + r"'\]\['val'\]='([^']*)'", text)
            f["value"] = v.group(1) if v else None
            lo = re.search(r"valSettings\['val" + re.escape(fid) + r"'\]\['min'\] = '([^']*)'", text)
            hi = re.search(r"valSettings\['val" + re.escape(fid) + r"'\]\['max'\] = '([^']*)'", text)
            if lo and hi:
                f["range"] = [lo.group(1), hi.group(1)]
        sliders = slider_blocks(body)
        if sliders:
            f["slots"] = sliders
        out.append(f)
    return out


def hhmm(units: int) -> str:
    return f"{units * 15 // 60:02d}:{units * 15 % 60:02d}"


def slider_blocks(text: str) -> list[dict]:
    """Time sliders: {"day": "Montag", "slots": ["00:00–24:00", …]} (128 = unused slot)."""
    out = []
    for m in re.finditer(r'<h2[^>]*>([^<]*)</h2>((?:(?!<h2).)*?)\.slider\(\{(.*?)\}\);', text, re.S):
        vals = re.search(r"values:\s*\[([^\]]*)\]", m.group(3))
        if not vals:
            continue
        nums = [int(x) for x in vals.group(1).split(",") if x.strip().lstrip("-").isdigit()]
        pairs = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        out.append({"day": clean(m.group(1)), "raw": nums,
                    "slots": [f"{hhmm(a)}–{hhmm(b)}" for a, b in pairs if a < 128 and b < 128]})
    return out


def modbus_holding(regs: list[tuple[int, int]]) -> dict[int, int | str]:
    """Raw holding registers (FC3), register numbers as the ISG docs count them (address + 1)."""
    out: dict[int, int | str] = {}
    with socket.create_connection((HOST, 502), timeout=5) as s:
        for tid, (start, count) in enumerate(regs, 1):
            pdu = struct.pack(">BHH", 3, start - 1, count)
            s.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, 1) + pdu)
            length = struct.unpack(">HHHB", s.recv(7))[2]
            body = b""
            while len(body) < length - 1:
                body += s.recv(length - 1 - len(body))
            if body[0] & 0x80:
                out[start] = f"exception {body[1]}"
                continue
            for i, v in enumerate(struct.unpack(f">{body[1] // 2}H", body[2:])):
                out[start + i] = v
    return out


def collect() -> dict:
    pages = {}
    for page in SETTINGS_PAGES + PROGRAM_PAGES:
        text = fetch(page)
        todo = [(page, text)] + [(p, fetch(p)) for p in subpages(page, text)]
        for p, t in todo:
            entry = {"title": page_title(t), "fields": fields(t)}
            if p.split(",")[0] == "3" and not entry["fields"]:
                entry["program"] = slider_blocks(t)
            pages[p] = entry
    try:
        modbus = modbus_holding([(1001, 27), (4001, 3)])
    except OSError as exc:
        modbus = {"error": str(exc)}
    return {"host": HOST, "saved": datetime.now().isoformat(timespec="seconds"), "pages": pages,
            "modbus_holding": modbus}


def as_markdown(b: dict) -> str:
    lines = [f"# ISG-Einstellungen {b['host']}", "", f"Gesichert {b['saved']} (nur gelesen).", ""]
    for p, e in b["pages"].items():
        lines += [f"## ?s={p} {e['title']}".rstrip(), ""]
        for f in e["fields"]:
            allowed = " | ".join(f["options"]) if "options" in f else "–".join(f.get("range", []))
            lines.append(f"- **{f['title']}**: {f.get('value') or '–'} {f['unit']}".rstrip()
                         + (f"  _(erlaubt: {allowed})_" if allowed else ""))
            for s in f.get("slots", []):
                lines.append(f"  - {s['day']}: {', '.join(s['slots']) or 'aus'}")
        for s in e.get("program", []):
            lines.append(f"- {s['day']}: {', '.join(s['slots']) or 'aus'}")
        lines.append("")
    lines += ["## Modbus Holding-Register (roh)", ""]
    lines += [f"- {k}: {v}" for k, v in b["modbus_holding"].items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    b = collect()
    OUT.mkdir(exist_ok=True)
    stem = OUT / f"isg-settings-{datetime.now():%Y-%m-%d}"
    stem.with_suffix(".json").write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    stem.with_suffix(".md").write_text(as_markdown(b), encoding="utf-8")
    n = sum(len(e["fields"]) + len(e.get("program", [])) for e in b["pages"].values())
    print(f"{n} settings and program days from {len(b['pages'])} pages -> {stem}.json / .md")


if __name__ == "__main__":
    main()

"""Energy-flow model built from the openWB topology (pure, no I/O).

Turns openWB's meter hierarchy plus live powers into nodes and directed edges
for the dashboard's flow diagram. Synthetic nodes are added for things openWB
doesn't list as components: the PV modules feeding each inverter, the house
(everything behind the grid meter that isn't a listed component, computed by
openWB) and the heat pump inside the house (no meter of its own).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .rules import de

# openWB component type -> dashboard node kind ("grid" is the public grid above the meter)
KINDS = {"counter": "meter", "inverter": "inverter", "bat": "battery", "cp": "charger"}
# short box titles; the device's own name (from openWB) goes into the first detail line
TITLES = {"meter": "Zähler", "inverter": "Wechselrichter", "battery": "Batterie", "charger": "Wallbox"}
REDUNDANT = (" Zähler", " Wechselrichter", " Speicher", " Batterie")   # said by the title already


@dataclass
class Node:
    id: str
    kind: str                      # grid | meter | inverter | pv | battery | charger | house | heatpump
    label: str
    parent: str | None
    power_w: float | None = None   # what the node itself draws (+) or supplies (−), for display
    details: list[str] = field(default_factory=list)
    role: str = ""                 # source | sink | idle | pass
    role_text: str = ""
    name: str = ""                 # the device's own name (openWB), shown as a detail


@dataclass
class Edge:
    source: str
    target: str
    w: float | None                # ≥ 0, flows source → target; None = not metered


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _fmt_w(w: float | None) -> str:
    if w is None:
        return "–"
    return f"{de(w / 1000, 2)} kW" if abs(w) >= 1000 else f"{w:.0f} W"


def build_flow(hierarchy: list | None, names: dict[int, str], powers: dict[int, float | None],
               home_w: float | None, kostal: dict | None = None, isg: dict | None = None,
               openwb: dict | None = None) -> dict:
    """hierarchy: openWB `counter/get/hierarchy`; powers: openWB power per component id
    (openWB signs: counter + import, pv − generation, bat + charging, cp + charging)."""
    kostal, isg, openwb = kostal or {}, isg or {}, openwb or {}
    nodes: list[Node] = []
    edges: list[Edge] = []

    def consumption(n: dict) -> float | None:
        """Power flowing from the parent into this subtree (+) or out of it (−)."""
        own = _f(powers.get(n["id"]))
        if n.get("type") == "inverter":   # batteries behind an inverter aren't in its PV power
            parts = [own] + [consumption(c) for c in n.get("children", [])]
            return None if None in parts else sum(parts)
        return own

    def add(n: dict, parent: str | None) -> None:
        kind = KINDS.get(n.get("type"), "meter")
        nid = f"c{n['id']}"
        flow_in = consumption(n)
        node = Node(nid, kind, TITLES.get(kind, kind), parent)
        node.name = names.get(n["id"]) or ""
        for word in REDUNDANT:
            node.name = node.name.removesuffix(word)
        if kind == "inverter":
            gen = _f(powers.get(n["id"]))
            pv_id = f"pv{n['id']}"
            node.power_w = None if flow_in is None else -flow_in        # AC output
            nodes.append(node)
            nodes.append(Node(pv_id, "pv", "PV-Module", nid, None if gen is None else -gen))
            edges.append(Edge(pv_id, nid, None if gen is None else max(0.0, -gen)))
        else:
            node.power_w = flow_in
            nodes.append(node)
        if parent is not None:
            _edge(edges, parent, nid, flow_in)
        for c in n.get("children", []):
            add(c, nid)

    roots = hierarchy or []
    for root in roots:
        grid_w = _f(powers.get(root["id"]))
        nodes.append(Node("utility", "grid", "Stromnetz", None, grid_w))
        add(root, "utility")
        root_id = f"c{root['id']}"
        root_node = next(x for x in nodes if x.id == root_id)
        root_node.label = "Netzanschluss"
        house = Node("house", "house", "Haus", root_id, home_w)
        nodes.append(house)
        _edge(edges, root_id, "house", home_w)
        hp_w = _f(isg.get("hp_power_w")) if isg else None
        hp = Node("heatpump", "heatpump", "Wärmepumpe", "house", hp_w)
        nodes.append(hp)
        edges.append(Edge("house", "heatpump", None if hp_w is None else max(0.0, hp_w)))
        break  # one grid connection

    _describe(nodes, kostal, isg, openwb)
    _roles(nodes)
    return {
        "source": "openWB" if roots else None,
        "nodes": [n.__dict__ for n in nodes],
        "edges": [{"from": e.source, "to": e.target, "w": e.w} for e in edges],
        "balance": build_balance(nodes),
    }


def build_balance(nodes: list[Node], min_w: float = 5.0) -> dict:
    """Split the site into what supplies power now and what uses it (both sides sum up)."""
    sources: list[dict] = []
    sinks: list[dict] = []
    for n in nodes:
        w = n.power_w
        if w is None or abs(w) < min_w:
            continue
        if n.kind == "grid":
            (sources if w > 0 else sinks).append(
                {"kind": "grid", "label": "Netzbezug" if w > 0 else "Einspeisung", "w": abs(w)})
        elif n.kind == "pv":
            sources.append({"kind": "pv", "label": "PV", "w": w})
        elif n.kind == "battery":
            (sinks if w > 0 else sources).append(
                {"kind": "battery", "label": "Batterie laden" if w > 0 else "Batterie entladen", "w": abs(w)})
        elif n.kind == "charger":
            sinks.append({"kind": "charger", "label": "Auto", "w": w})
        elif n.kind == "house":
            hp = next((x.power_w for x in nodes if x.kind == "heatpump" and x.power_w is not None), None)
            hp = min(max(0.0, hp), w) if hp is not None else 0.0          # the heat pump is part of the house load
            if w - hp >= min_w:
                sinks.append({"kind": "house", "label": "Haus (ohne WP)" if hp else "Haus", "w": w - hp})
            if hp >= min_w:
                sinks.append({"kind": "heatpump", "label": "Wärmepumpe", "w": hp})
    return {"sources": sources, "sinks": sinks,
            "total_w": max(sum(s["w"] for s in sources), sum(s["w"] for s in sinks))}


def _roles(nodes: list[Node]) -> None:
    """Mark each node as producer, consumer, storage or pass-through for the diagram."""
    for n in nodes:
        w = n.power_w
        idle = w is None or abs(w) < 20
        if n.kind == "pv":
            n.role, n.role_text = ("idle", "ruht") if idle else ("source", "erzeugt")
        elif n.kind == "battery":
            n.role, n.role_text = ("idle", "ruht") if idle else (("sink", "lädt") if w > 0 else ("source", "entlädt"))
        elif n.kind == "grid":
            n.role, n.role_text = ("idle", "ausgeglichen") if idle else (("source", "Bezug") if w > 0 else ("sink", "Einspeisung"))
        elif n.kind in ("house", "charger"):
            n.role, n.role_text = ("idle", "ruht") if idle else ("sink", "verbraucht")
        elif n.kind == "heatpump":
            if n.power_w is None:
                n.role, n.role_text = "sink", "Teil des Hauses"
            else:
                n.role, n.role_text = ("sink", "verbraucht") if n.power_w >= 20 else ("idle", "ruht")
        else:  # meter, inverter
            n.role, n.role_text = "pass", "misst" if n.kind == "meter" else "wandelt"


def fallback_hierarchy() -> tuple[list, dict[int, str]]:
    """Topology to use when openWB is unreachable: grid meter → inverter → battery."""
    return ([{"id": 0, "type": "counter", "children": [
        {"id": 2, "type": "inverter", "children": [{"id": 4, "type": "bat", "children": []}]}]}],
        {0: "Kostal-Zähler", 2: "Kostal Plenticore", 4: "Kostal-Batterie"})


def kostal_powers(k: dict) -> tuple[dict[int, float | None], float | None]:
    """Kostal values in openWB sign convention for the fallback topology."""
    bat = _f(k.get("battery_power"))
    parts = [_f(k.get("home_from_pv")), _f(k.get("home_from_battery")), _f(k.get("home_from_grid"))]
    return ({0: _f(k.get("grid_power")),
             2: None if k.get("pv_power") is None else -float(k["pv_power"]),
             4: None if bat is None else -bat},
            None if None in parts else sum(parts))


def _edge(edges: list[Edge], parent: str, child: str, signed: float | None) -> None:
    if signed is None:
        edges.append(Edge(parent, child, None))
    elif signed >= 0:
        edges.append(Edge(parent, child, signed))
    else:
        edges.append(Edge(child, parent, -signed))


def _describe(nodes: list[Node], k: dict, i: dict, o: dict) -> None:
    for n in nodes:
        d = n.details
        if n.kind == "grid":
            pass
        elif n.kind == "pv":
            if k.get("dc1_power") is not None:
                d.append(f"String 1: {_fmt_w(k['dc1_power'])} · String 2: {_fmt_w(k.get('dc2_power'))}")
            if k.get("yield_day") is not None:
                d.append(f"heute {de(k['yield_day'])} kWh")
        elif n.kind == "meter":
            d.append(n.name or "Zähler")
        elif n.kind == "inverter":
            d.append(n.name or "AC-Leistung")
        elif n.kind == "battery":
            if k.get("battery_soc") is not None:
                d.append(f"{k['battery_soc']:.0f} % von 10,2 kWh")
            if n.name:
                d.append(n.name)
        elif n.kind == "charger":
            plugged = o.get("cp_plugged")
            state = None if plugged is None else (
                ("lädt" if o.get("cp_charging") == "ja" else "angesteckt") if plugged == "ja" else "kein Fahrzeug")
            d.append(" · ".join(x for x in (n.name, o.get("cp_mode")) if x))
            today = None if o.get("cp_daily") is None else f"heute {de(o['cp_daily'])} kWh"
            d.append(" · ".join(x for x in (state, today) if x))
            n.details = [x for x in d if x]
        elif n.kind == "house":
            d.append("ohne Ladepunkt")
        elif n.kind == "heatpump":
            dhw = None if i.get("dhw_temp") is None else de(i["dhw_temp"])
            flow = None if i.get("flow_temp") is None else de(i["flow_temp"])
            kw, hz = i.get("compressor_kw"), i.get("compressor_hz")
            if kw and hz:                       # running: the live compressor first (FACHMANN page, every minute)
                cop = i.get("cop_live")
                d.append(" · ".join(x for x in (f"Verdichter {de(kw, 2)} kW", f"{hz:.0f} Hz",
                                                cop and f"COP {de(cop)}") if x))
                d.append(" · ".join(x for x in (dhw and f"WW {dhw} °C", flow and f"Vorlauf {flow} °C") if x))
            else:
                if dhw:
                    d.append(f"Warmwasser {dhw} °C (Soll {de(i.get('dhw_target') or 0)})")
                if flow:
                    ret = f" → {de(i['return_temp'])}" if i.get("return_temp") is not None else ""
                    d.append(f"Fußbodenheizung {flow}{ret} °C")

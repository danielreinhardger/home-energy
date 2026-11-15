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

from .i18n import fnum, t


# openWB component type -> dashboard node kind ("grid" is the public grid above the meter)
KINDS = {"counter": "meter", "inverter": "inverter", "bat": "battery", "cp": "charger"}
# short box titles are the texts "flow.title.<kind>"; the device's own name (from openWB) is kept on the
# node without a trailing word the title already says (openWB names, German or English), but not shown
REDUNDANT = (" Zähler", " Wechselrichter", " Speicher", " Batterie", " Meter", " Counter", " Inverter", " Battery")


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
    name: str = ""                 # the device's own name (openWB); the boxes don't show it


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
    return f"{fnum(w / 1000, 2)} kW" if abs(w) >= 1000 else f"{w:.0f} W"


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
        node = Node(nid, kind, t(f"flow.title.{kind}"), parent)
        node.name = names.get(n["id"]) or ""
        for word in REDUNDANT:
            node.name = node.name.removesuffix(word)
        if kind == "inverter":
            gen = _f(powers.get(n["id"]))
            pv_id = f"pv{n['id']}"
            node.power_w = None if flow_in is None else -flow_in        # AC output
            nodes.append(node)
            nodes.append(Node(pv_id, "pv", t("flow.pv_module"), nid, None if gen is None else -gen))
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
        nodes.append(Node("utility", "grid", t("flow.stromnetz"), None, grid_w))
        add(root, "utility")
        root_id = f"c{root['id']}"
        root_node = next(x for x in nodes if x.id == root_id)
        root_node.label = t("flow.netzanschluss")
        house = Node("house", "house", t("flow.haus"), root_id, home_w)
        nodes.append(house)
        _edge(edges, root_id, "house", home_w)
        hp_w = _f(isg.get("hp_power_w")) if isg else None
        hp = Node("heatpump", "heatpump", t("flow.waermepumpe"), "house", hp_w)
        nodes.append(hp)
        edges.append(Edge("house", "heatpump", None if hp_w is None else max(0.0, hp_w)))
        break  # one grid connection

    nodes.sort(key=lambda n: n.kind == "charger")     # stable: the wallbox stands to the right of the house
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
                {"kind": "grid", "label": t("flow.netzbezug") if w > 0 else t("flow.einspeisung"), "w": abs(w)})
        elif n.kind == "pv":
            sources.append({"kind": "pv", "label": "PV", "w": w})
        elif n.kind == "battery":
            (sinks if w > 0 else sources).append(
                {"kind": "battery", "label": t("flow.batterie_laden") if w > 0 else t("flow.batterie_entladen"), "w": abs(w)})
        elif n.kind == "charger":
            sinks.append({"kind": "charger", "label": t("flow.auto"), "w": w})
        elif n.kind == "house":
            hp = next((x.power_w for x in nodes if x.kind == "heatpump" and x.power_w is not None), None)
            hp = min(max(0.0, hp), w) if hp is not None else 0.0          # the heat pump is part of the house load
            if w - hp >= min_w:
                sinks.append({"kind": "house", "label": t("flow.haus_ohne_wp") if hp else t("flow.haus"), "w": w - hp})
            if hp >= min_w:
                sinks.append({"kind": "heatpump", "label": t("flow.waermepumpe"), "w": hp})
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
            n.role, n.role_text = ("idle", "ruht") if idle else (("sink", t("flow.laedt")) if w > 0 else ("source", t("flow.entlaedt")))
        elif n.kind == "grid":
            n.role, n.role_text = ("idle", "ausgeglichen") if idle else (("source", t("flow.bezug")) if w > 0 else ("sink", t("flow.einspeisung")))
        elif n.kind in ("house", "charger"):
            n.role, n.role_text = ("idle", "ruht") if idle else ("sink", "verbraucht")
        elif n.kind == "heatpump":
            if n.power_w is None:
                n.role, n.role_text = "sink", t("flow.teil_des_hauses")
            else:
                n.role, n.role_text = ("sink", "verbraucht") if n.power_w >= 20 else ("idle", "ruht")
        else:  # meter, inverter
            n.role, n.role_text = "pass", "misst" if n.kind == "meter" else "wandelt"


def fallback_hierarchy() -> tuple[list, dict[int, str]]:
    """Topology to use when openWB is unreachable: grid meter → inverter → battery."""
    return ([{"id": 0, "type": "counter", "children": [
        {"id": 2, "type": "inverter", "children": [{"id": 4, "type": "bat", "children": []}]}]}],
        {0: t("flow.kostal_zaehler"), 2: "Kostal Plenticore", 4: t("flow.kostal_batterie")})


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
                d.append(t("flow.strings", dc1=_fmt_w(k["dc1_power"]), dc2=_fmt_w(k.get("dc2_power"))))
            if k.get("yield_day") is not None:
                d.append(t("flow.heute_kwh", yield_day=fnum(k['yield_day'])))
        elif n.kind == "meter":
            d.append(t("flow.zaehler"))
        elif n.kind == "inverter":
            d.append(t("flow.ac_leistung"))
        elif n.kind == "battery":
            if k.get("battery_soc") is not None:
                cap = k.get("battery_capacity")
                d.append(t("flow.battery_soc_of", soc=fnum(k["battery_soc"], 0), kwh=fnum(cap / 1000, 1)) if cap
                         else t("flow.battery_soc", soc=fnum(k["battery_soc"], 0)))
        elif n.kind == "charger":
            plugged = o.get("cp_plugged")
            state = None if plugged is None else (
                (t("flow.laedt") if o.get("cp_charging") == 1 else t("flow.angesteckt")) if plugged == 1 else t("flow.kein_fahrzeug"))
            d.append(o.get("cp_mode"))
            today = None if o.get("cp_daily") is None else t("flow.heute_kwh_2", cp_daily=fnum(o['cp_daily']))
            d.append(" · ".join(x for x in (state, today) if x))
            n.details = [x for x in d if x]
        elif n.kind == "heatpump":
            dhw = None if i.get("dhw_temp") is None else fnum(i["dhw_temp"])
            flow = None if i.get("flow_temp") is None else fnum(i["flow_temp"])
            kw, hz = i.get("compressor_kw"), i.get("compressor_hz")
            if kw and hz:                       # running: the live compressor first (FACHMANN page, every minute)
                cop = i.get("cop_live")
                d.append(" · ".join(x for x in (t("flow.verdichter_kw", kw=fnum(kw, 2)), f"{fnum(hz, 0)} Hz",
                                                cop and t("flow.cop", cop=fnum(cop))) if x))
                d.append(" · ".join(x for x in (dhw and t("flow.dhw_short", dhw=dhw), flow and t("flow.vorlauf_c", flow=flow)) if x))
            else:
                if dhw:
                    d.append(t("flow.warmwasser_c_soll", dhw=dhw, value=fnum(i.get('dhw_target') or 0)))
                if flow:
                    ret = f" → {fnum(i['return_temp'])}" if i.get("return_temp") is not None else ""
                    d.append(t("flow.fussbodenheizung_c", flow=flow, ret=ret))

"""Read model for the web layer: turns the service's state into the JSON the pages show.

Only reads; never talks to a device. Kept apart from the service so that what the pages
see can change without touching polling or control.
"""
from __future__ import annotations

import hashlib
import json

from dataclasses import replace
from typing import TYPE_CHECKING

from .i18n import t
from . import config as config_mod
from . import i18n
from .settings import WORD_UNITS
from . import heating as heating_rules
from . import hotwater as hotwater_rules
from .devices.openwb import OpenWbDevice
from .economics import merit_order, money_today
from .flow import build_flow, fallback_hierarchy, kostal_powers
from .hotwater import bath_plan
from .polling import Poller
from .registers import (device_name, group_label, option_texts, page_only_texts, point_label,
                        setting_label, setting_note)
from .store import to_ts

if TYPE_CHECKING:
    from .service import Service

HISTORY_HOURS = 48                 # what the charts load at once (one sample per minute)


def energy(poller: Poller) -> dict[str, float | None]:
    """The values the charts and the history keep (one sample)."""
    k, i, o, w = poller.latest("kostal"), poller.latest("isg"), poller.ok("openwb"), poller.ok("isg_web")
    parts = [k.get("home_from_pv"), k.get("home_from_battery"), k.get("home_from_grid")]
    kostal_home = sum(parts) if None not in parts else None
    car = o.get("cp_power")
    # The Kostal's house figure includes the wallbox (it sits behind the same meter);
    # openWB's home consumption doesn't. Prefer openWB, else subtract the car.
    house = o.get("home_power")
    if house is None and kostal_home is not None:
        house = kostal_home - (car or 0.0)
    return {
        "pv_w": k.get("pv_power"),
        "house_w": house,
        "car_w": car,
        "grid_w": k.get("grid_power"),
        "battery_w": k.get("battery_power"),
        "soc": k.get("battery_soc"),
        "dhw_temp": i.get("dhw_temp"),
        "dhw_target": i.get("dhw_target"),
        "outside_temp": i.get("outside_temp"),
        "hp_w": w.get("hp_power_w"),
        # live heat output and COP (FACHMANN page): how efficient each run really is
        "hp_heat_w": None if w.get("heat_output_kw") is None else w["heat_output_kw"] * 1000,
        "cop_live": w.get("cop_live"),
        "cop_heating_live": w.get("cop_live") if w.get("hp_mode") == "heating" else None,
        # kept for calibrating the house model (how fast does it cool at night?)
        # (until 2026-11-11 "room_temp" held the ISG's echo of its own room target – no measurement;
        #  the real room temperature is added by the service as "room_sensor")
        "extract_temp": i.get("extract_temp"),  # extract air (from the rooms): indoor proxy while fans run
        "floor_flow": i.get("flow_temp"),
        "floor_return": i.get("return_temp"),
    }


def hygiene_view(cfg, state, now) -> dict:
    """Anti-legionella for the page: due now, or when it last reached the temperature."""
    from .hotwater import hygiene_status
    due, overdue, last = hygiene_status(cfg, state, now)
    return {"enabled": cfg.hygiene_enabled, "due": due, "overdue": overdue,
            "last": last.isoformat(timespec="minutes") if state.hygiene_at and not due else None}


def day_totals(poller: Poller) -> dict | None:
    """Today's running counters for the ledger (None while openWB is unreachable)."""
    o, k, w = poller.ok("openwb"), poller.latest("kostal"), poller.ok("isg_web")
    if not o:
        return None
    return {"import_kwh": o.get("grid_import_day"), "export_kwh": o.get("grid_export_day"),
            "pv_kwh": k.get("yield_day"), "house_kwh": o.get("home_day"), "car_kwh": o.get("cp_daily"),
            "hp_heat_heating_kwh": w.get("heat_heating_day"), "hp_power_heating_kwh": w.get("power_heating_day"),
            "hp_heat_dhw_kwh": w.get("heat_dhw_day"), "hp_power_dhw_kwh": w.get("power_dhw_day")}


def snapshot(svc: "Service") -> dict:
    p, ctl, cfg = svc.poller, svc.control, svc.cfg
    with svc.lock:
        devices = {}
        for key, dev in p.devices.items():
            st = p.status[key]
            vals = st.values or {}
            devices[key] = {
                "name": device_name(key), "host": dev.host, "error": st.error, "updated": st.updated,
                "stale": not p.fresh(key),
                "values": {pt.key: vals.get(pt.key) for pt in dev.spec.points},   # labels: meta()
            }
        fc = svc.forecaster.summary()
        return {
            "now": svc.clock().isoformat(timespec="seconds"),
            "energy": energy(p),
            "devices": devices,
            "heating": {"last": ctl.heating.last, "writes_today": ctl.heating.state.writes_today,
                        "config": cfg.heating.__dict__},
            "controller": {"last": ctl.dhw.last, "writes_today": ctl.dhw.state.writes_today,
                           "config": cfg.dhw.__dict__, "power": ctl.dhw_power.last,
                           "hygiene": hygiene_view(cfg.dhw, ctl.dhw.state.strategy, svc.clock())},
            "meta_version": meta(svc)["version"],
            "forecast": {"enabled": cfg.forecast.usable, "error": svc.forecaster.error,
                         "summary": fc.to_json() if fc else None, "accuracy": svc.forecaster.accuracy()},
            "flow": flow(p),
            "economy": economy(svc),
            "bath": bath(svc),
            "alerts": svc.alerts.to_json(cfg.alerts),
            "backup_heater": dict(svc.backup.today),          # kWh of the electric heater today
            "room": room(svc),
        }


def point_meta(device: str, pt) -> dict:
    """Label, unit, group and digits of one device value – any device kind (Modbus, MQTT, web page) –
    in the current language; status codes also carry their texts ("options")."""
    out = {"key": pt.key, "label": point_label(device, pt.key), "unit": pt.unit,
           "group": group_label(pt.group), "digits": pt.digits}
    if pt.options:
        out["options"] = option_texts(f"dev.{device}.{pt.key}", pt.options)
    return out


_meta_cache: tuple[object, dict] | None = None        # (the config it was built from, meta)


def meta(svc: "Service") -> dict:
    """What rarely changes: each device value's label/unit/group/digits and both strategies'
    rule texts (they contain config values, so they change with the settings). Built once per
    config; the state carries only `version`, so the page reloads this when it changed."""
    global _meta_cache
    cfg = svc.cfg
    if _meta_cache and _meta_cache[0] is cfg:
        return _meta_cache[1]
    body = {
        "devices": {key: [point_meta(key, pt) for pt in dev.spec.points]
                    for key, dev in svc.poller.devices.items()},
        "explain": {"dhw": explain(hotwater_rules, cfg.dhw), "heating": explain(heating_rules, cfg.heating)},
        "language": i18n.language(), "locale": i18n.locale(), "texts": i18n.web_texts(),
        "background": cfg.ui.background,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    out = {"version": hashlib.sha1(raw).hexdigest()[:12], **body}
    _meta_cache = (cfg, out)
    return out


DECISION_FIELDS = ("time", "mode", "target", "device_target", "action", "reasons")


def decisions(svc: "Service", limit: int = 30) -> dict:
    """Both decision logs for the PV-Regelung tab, newest first, without trace and metrics."""
    ctl = svc.control
    slim = lambda entries: [{k: e.get(k) for k in DECISION_FIELDS} for e in list(entries)[:limit]]
    with svc.lock:                                       # the control thread appends meanwhile
        return {"dhw": slim(ctl.dhw.recent), "heating": slim(ctl.heating.recent)}


def explain(rules, cfg) -> dict:
    """A strategy's steps, outcome names and off text for the explanation tab (texts live next to the rules)."""
    modes = rules.DhwMode if rules is hotwater_rules else rules.HeatMode
    return {"stages": rules.stages(cfg), "outcomes": rules.outcome_texts(), "off": rules.off_text(),
            "modes": {m.value: rules.mode_label(m) for m in modes}}


def flow(p: Poller) -> dict:
    """Energy-flow diagram data from the openWB topology (Kostal-only fallback)."""
    k = p.latest("kostal")
    i = {**p.latest("isg"), **p.ok("isg_web")}
    o = p.ok("openwb")
    ow = p.devices.get("openwb")
    if o and isinstance(ow, OpenWbDevice) and ow.hierarchy():
        return build_flow(ow.hierarchy(), ow.component_names(), ow.component_powers(), o.get("home_power"), k, i, o)
    hierarchy, names = fallback_hierarchy()
    powers, home = kostal_powers(k)
    out = build_flow(hierarchy, names, powers, home, k, i, o)
    out["source"] = t("view.kostal_openwb_nicht_erreichbar")
    return out


def economy(svc: "Service") -> dict:
    cfg = svc.cfg
    cop = svc.cop_model()
    outside = svc.poller.latest("isg").get("outside_temp")
    weather = svc.forecaster.weather
    day = weather.daily[0] if weather and weather.daily else None
    gain = cop.day_gain(day.tmin, day.tmax) if day else None
    eco = cfg.economy if gain is None else replace(cfg.economy, day_cop_gain=gain)
    cop_h, cop_w = cop.heating(outside), cop.dhw_cop
    order = merit_order(eco, cop_h, cop_w, cfg.dhw.comfort_temp, cfg.dhw.boost_temp)
    o = svc.poller.ok("openwb")
    return {"cop_heating": round(cop_h, 2), "cop_dhw": round(cop_w, 2), "cop_source": cop.source,
            "cop_outside": outside, "cop_scale": round(cop.heating_scale, 2),
            "day_cop_gain": round(eco.day_cop_gain, 3), "day_gain_from_forecast": gain is not None,
            "day_gain_measured": bool(day and cop.day_gain_measured(day.tmin, day.tmax)),
            "cop_live": None if cop.live is None else {"minutes": cop.live.minutes, "per_k": cop.live.per_k,
                                                       "range": [cop.live.t_lo, cop.live.t_hi]},
            "cop_curve": [{"t": t, "cop": round(cop.heating(t), 2)} for t in (-10, -5, 0, 5, 10, 15)],
            "prices": {"buy": cfg.economy.price_buy, "feed": cfg.economy.price_feed},
            "merit_order": [u.__dict__ for u in order],
            "today": money_today(o.get("grid_import_day"), o.get("grid_export_day"), cfg.economy)}


def bath(svc: "Service") -> dict:
    cfg = svc.cfg.dhw
    view = {"default_time": cfg.bath_default_time, "default_temp": cfg.bath_temp, "request": None}
    req_obj = svc.control.bath
    if req_obj is not None:
        req = req_obj.to_json()
        plan = bath_plan(req_obj, svc.poller.latest("isg").get("dhw_temp"), cfg)
        req["latest_start"] = plan["latest_start"].strftime("%H:%M")
        req["heat_minutes"] = round(plan["heat_min"])
        req["status"] = ((svc.control.dhw.last or {}).get("metrics") or {}).get("bath", {}).get("status")
        view["request"] = req
    return view


def settings(svc: "Service") -> dict:
    cfg, p = svc.cfg, svc.poller
    with svc.lock:
        out = {}
        for key, dev in p.devices.items():
            current = p.settings(key)
            out[key] = {
                "name": device_name(key),
                "page_only": page_only_texts(key, dev.spec.page_only),
                "settings": [
                    {"key": s.key, "label": setting_label(key, s.key), "group": group_label(s.group), "unit": s.unit,
                     "min": s.minimum, "max": s.maximum, "step": s.step,
                     "options": option_texts(f"dev.{key}.set.{s.key}", s.options),
                     "writable": s.writable, "verified": s.verified, "note": setting_note(key, s.key),
                     "controlled": key == "isg" and svc.controlled_setting(s.key),
                     "value": current.get(s.key)}
                    for s in dev.spec.settings],
            }
        return {"devices": out,
                "config": {sec: {"values": getattr(cfg, sec).__dict__ if sec != "forecast"
                                 else {**cfg.forecast.__dict__, "arrays": [a.__dict__ for a in cfg.forecast.arrays]},
                                 "fields": field_texts(sec, meta)}
                           for sec, meta in config_mod.UI_FIELDS.items()}}


def field_texts(section: str, fields: dict[str, dict]) -> dict[str, dict]:
    """The settings forms' metadata with each field's label, help and unit in the current language."""
    out = {}
    for name, meta in fields.items():
        key = f"cfg.{section}.{name}"
        m = dict(meta, label=i18n.t(key))
        if i18n.has(key + ".help"):
            m["help"] = i18n.t(key + ".help")
        if m.get("unit") in WORD_UNITS:
            m["unit"] = i18n.t(f"unit.{m['unit']}")
        out[name] = m
    return out


def perf(svc: "Service") -> dict:
    """Timings and sizes for checking the runtime cost (GET /api/perf)."""
    files = {f.name: round(f.stat().st_size / 1024, 1) for f in sorted(svc.data_dir.iterdir()) if f.is_file()}
    devices = svc.poller.perf()
    with svc.lock:
        return {
            "devices": devices,
            "control_ms": svc.perf["control_ms"], "control_ms_max": svc.perf["control_ms_max"],
            "control_skipped": svc.control.skipped,
            "db_rows": {t: svc.store.count(t) for t in ("samples", "samples_15m", "days", "decisions")},
            "api": {k: dict(v) for k, v in svc.perf["api"].items()},
            "files_kb": files,
        }


def room(svc: "Service") -> dict:
    """The room sensor card: last reading, its age, and the value the heating strategy uses."""
    h = svc.cfg.heating
    view = svc.room.view(svc.clock(), h.room_sensor_max_age_min)
    view["source"] = t("view.waermepumpe_raumbedienung") if h.room_from_isg else t("view.raumfuehler")
    view["used"] = svc.room_temp()
    return view


def finance(svc: "Service") -> dict:
    """The money ledger for the Finanzen tab: last 62 days, all months, the current prices."""
    with svc.lock:
        return {"days": svc.ledger.day_rows(62), "months": svc.ledger.month_rows(),
                "prices": {"buy": svc.cfg.economy.price_buy, "feed": svc.cfg.economy.price_feed}}


def history(svc: "Service", since: str | None = None, columns: bool = False) -> list[dict] | dict:
    """Minute samples of the last 48 h, or only those after `since` (ISO time or unix seconds)
    for incremental updates; `columns`: one list per value (what the page loads)."""
    try:
        start = (int(since) if since and since.isdigit() else to_ts(since)) if since \
            else to_ts(svc.clock()) - HISTORY_HOURS * 3600
    except ValueError:
        raise ValueError(t("view.since_iso_zeit_oder_unix_sekunden")) from None
    return svc.store.samples(start, columns=columns)


def history_range(svc: "Service", start: str, end: str | None = None, points: int = 800,
                  columns: bool = False) -> dict:
    """Any time range, thinned to about `points` rows (minute or 15-minute data underneath)."""
    try:
        a = to_ts(start)
        b = to_ts(end) if end else to_ts(svc.clock())
    except ValueError:
        raise ValueError(t("view.from_to_iso_zeit_erwartet")) from None
    if b <= a:
        raise ValueError("from muss vor to liegen")
    return svc.store.series(a, b, max(50, min(int(points), 3000)), columns=columns)

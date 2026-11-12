// Overview tab (without the time charts, see timeline.js): tiles, energy flow, the two
// control decisions, bath request, forecast and today's money.
import { api, el, fmt, power, dayTime, clear, deNum } from "./lib.js";
import { barChart } from "./charts.js";
import { renderFlow, renderBalance } from "./flow.js";
import { moneyBoxes } from "./finance.js";
import { icon, weatherIcon, weatherKind } from "./icons.js";
import { openOutlook } from "./outlook.js";

const $ = (id) => document.getElementById(id);

function tile(label, value, unit, sub, color, sub2) {
  return el("div", { class: "tile" },
    el("div", { class: "label" }, color ? el("i", { class: "key", style: `background:var(${color})` }) : null, label),
    el("div", { class: "value" }, value, el("small", {}, unit)),
    sub ? el("div", { class: "sub" }, sub) : null,
    sub2 ? el("div", { class: "sub" }, sub2) : null);
}

// The living room from the room sensor (the heat pump only echoes its target, so that's never shown as "Raum").
function roomTile(room, target) {
  const goal = `Ziel ${fmt(target, 1, "°C")}`;
  if (room?.used != null) {
    return tile("Raum", fmt(room.used, 1), "°C",
      [goal, room.humidity != null ? `Feuchte ${fmt(room.humidity, 0)} %` : null].filter(Boolean).join(" · "));
  }
  const why = room?.temp != null ? `Raumfühler: letzte Meldung vor ${fmt(room.age_min, 0)} min` : "kein Raumfühler";
  return tile("Raum", "–", "", `${goal} · ${why}`);
}

// The electric backup heater (1 kWh electricity = 1 kWh heat): only shown on days it ran.
function backupText(b) {
  const total = b ? (b.heating ?? 0) + (b.dhw ?? 0) : 0;
  return total >= 1 ? `Heizstab heute ${fmt(total, 0)} kWh` : null;
}

// While the compressor runs: its measured heat output and heat ÷ motor power (FACHMANN page, every minute).
// The motor power leaves out fans and pumps, so this reads higher than the day's counter COP.
function liveCop(web) {
  if (web.cop_live == null) return null;
  return `jetzt ${fmt(web.heat_output_kw, 1)} kW Wärme · COP ${fmt(web.cop_live, 1)}`;
}

/** Draw everything on the overview tab from one state; `refresh` reloads the state (after a bath change). */
export function renderOverview(s, refresh) {
  renderTiles(s);
  renderBalance($("balance"), s.flow?.balance);
  renderFlow($("flow"), s.flow);
  renderBath(s.bath, s.controller.config, refresh);
  // only worth a note when openWB is down and the diagram falls back to Kostal data
  $("flow-source").textContent = s.flow?.source && s.flow.source !== "openWB" ? s.flow.source : "";
  renderDecision(s.controller);
  renderForecast(s.forecast, s.now);
  renderHeating(s.heating);
  if (s.economy) clear($("money"), moneyBoxes(s.economy));
}

function renderTiles(s) {
  const e = s.energy;
  const p = (w) => power(w);
  const bat = e.battery_w, grid = e.grid_w;
  const isg = Object.fromEntries((s.devices.isg?.values || []).map((v) => [v.key, v.value]));
  const ko = Object.fromEntries((s.devices.kostal?.values || []).map((v) => [v.key, v.value]));
  const ow = Object.fromEntries((s.devices.openwb?.values || []).map((v) => [v.key, v.value]));
  const web = Object.fromEntries((s.devices.isg_web?.values || []).map((v) => [v.key, v.value]));
  const kwh = (v) => fmt(v, 1);
  // heat pump: measured from its own Wh counters (ISG page), 5-minute average
  const hpW = e.hp_w, hpRuns = hpW !== null && hpW !== undefined && hpW >= 50;
  const backup = s.backup_heater ? (s.backup_heater.heating ?? 0) + (s.backup_heater.dhw ?? 0) : 0;
  const hpToday = (web.power_heating_day ?? 0) + (web.power_dhw_day ?? 0) + backup;
  const copToday = (heat, pow) => (pow >= 0.5 && heat >= pow ? fmt(heat / pow, 1) : "–");

  clear($("tiles-energy"),
    tile("PV", p(e.pv_w).v, p(e.pv_w).u, `heute ${fmt(ko.yield_day, 1, "kWh")}`, "--prod-pv"),
    tile("Haus", p(e.house_w).v, p(e.house_w).u, "ohne Wallbox, mit Wärmepumpe", "--cons-house"),
    tile("Wärmepumpe", hpW === null || hpW === undefined ? "–" : p(hpW).v, hpW === null || hpW === undefined ? "" : p(hpW).u,
      hpW === null || hpW === undefined ? "Messung startet (≈ 2 min)"
        : `${hpRuns ? "läuft" : "aus"} · heute ${kwh(hpToday)} kWh (Heizen ${kwh(web.power_heating_day)}, WW ${kwh(web.power_dhw_day)}` +
          (backup >= 1 ? `, Heizstab ${kwh(backup)})` : ")"),
      "--cons-hp", liveCop(web)),
    tile("Batterie", fmt(e.soc, 0), "%",
      bat == null ? null : bat < -20 ? `lädt ${p(-bat).v} ${p(-bat).u}` : bat > 20 ? `entlädt ${p(bat).v} ${p(bat).u}` : "Ruhe",
      bat != null && bat < -20 ? "--cons-bat" : "--prod-bat"),
    tile("Netz", p(Math.abs(grid ?? 0)).v, p(Math.abs(grid ?? 0)).u,
      grid == null ? null : grid > 20 ? "Bezug" : grid < -20 ? "Einspeisung" : "ausgeglichen",
      grid != null && grid < -20 ? "--cons-grid" : "--prod-grid"),
    s.devices.openwb ? tile("Auto", p(e.car_w).v, p(e.car_w).u,
      [ow.cp_mode, ow.cp_plugged === "ja" ? (ow.cp_charging === "ja" ? "lädt" : "angesteckt") : "kein Fahrzeug"]
        .filter(Boolean).join(" · "), "--cons-car") : null,
  );
  clear($("tiles-heat"),
    roomTile(s.room, isg.room_hc1_target),
    tile("Fußbodenheizung", fmt(isg.flow_temp, 1), "°C", `Vorlauf · Rücklauf ${fmt(isg.return_temp, 1, "°C")}`),
    tile("Warmwasser", fmt(e.dhw_temp, 1), "°C", `Soll ${fmt(e.dhw_target, 1, "°C")}`),
    tile("Außen", fmt(e.outside_temp, 1), "°C", e.outside_temp !== null && e.outside_temp < (s.heating?.config?.outside_max ?? 15)
      ? "Heizperiode" : "keine Heizperiode"),
    tile("Wärme heute", kwh((web.heat_heating_day ?? 0) + (web.heat_dhw_day ?? 0)), "kWh",
      `COP Heizen ${copToday(web.heat_heating_day ?? 0, web.power_heating_day ?? 0)} · WW ${copToday(web.heat_dhw_day ?? 0, web.power_dhw_day ?? 0)}`,
      null, backupText(s.backup_heater)),
  );
}

// ---- the two control decisions ---------------------------------------------------
function renderDecision(c) {
  const d = c.last;
  if (!d) return clear($("decision"), el("p", { class: "msg" }, "Erste Entscheidung folgt innerhalb einer Minute."));
  const m = d.metrics || {};
  clear($("decision"),
    el("div", {}, el("span", { class: "mode" }, c.explain.modes[d.mode] || d.mode, " → ", fmt(d.target, 1, "°C")), " ",
      d.dry_run ? el("span", { class: "badge" }, "Testbetrieb") : null),
    el("ul", { class: "reasons" }, d.reasons.map((r) => el("li", {}, deNum(r)))),
    el("dl", { class: "kv" },
      el("dt", {}, "Aktion"), el("dd", {}, d.action),
      el("dt", {}, "Batterie bis voll"), el("dd", {}, fmt(m.battery_need_kwh, 1, "kWh")),
      el("dt", {}, `Warmwasser bis ${fmt(m.boost_target, 0, "°C")}`), el("dd", {}, fmt(m.boost_need_kwh, 1, "kWh Strom")),
      c.power ? [el("dt", {}, "WW-Leistung"), el("dd", {}, `${fmt(c.power.current ?? c.power.target, 0)} % – ${c.power.reason}` +
        (c.power.action && c.power.action !== "unverändert" ? ` (${c.power.action})` : ""))] : null,
      c.hygiene?.enabled ? [el("dt", {}, "Legionellenschutz"), el("dd", {}, c.hygiene.overdue
        ? "überfällig – heizt mittags auch ohne PV"
        : c.hygiene.due ? "fällig – wartet auf PV-Überschuss"
        : `zuletzt ${new Date(c.hygiene.last).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })}`)] : null));
}

function renderHeating(h) {
  const box = $("heating");
  if (!h) return clear(box);
  const d = h.last, c = h.config;
  if (!d) return clear(box, el("p", { class: "msg" }, "Erste Entscheidung folgt innerhalb einer Minute."));
  const m = d.metrics || {};
  clear(box,
    el("div", {}, el("span", { class: "mode" }, h.explain.modes[d.mode] || d.mode, " → ", fmt(d.target, 1, "°C")), " ",
      d.dry_run ? el("span", { class: "badge" }, "Testbetrieb") : null),
    el("ul", { class: "reasons" }, d.reasons.map((r) => el("li", {}, deNum(r)))),
    el("dl", { class: "kv" },
      el("dt", {}, "Außen / Raum"), el("dd", {}, `${fmt(m.outside_temp, 1, "°C")} / ${fmt(m.room_temp, 1, "°C")}`),
      el("dt", {}, "Band"), el("dd", {}, `${fmt(c.room_min, 1)} … ${fmt(c.normal_temp, 1)} … ${fmt(c.room_max, 1)} °C (Heizperiode unter ${fmt(c.outside_max, 0)} °C)`),
      el("dt", {}, "Puffer jetzt bis"), el("dd", {}, `${fmt(m.boost_target, 1, "°C")} – ${deNum(m.buffer_reason || "")}`),
      el("dt", {}, "Aktion"), el("dd", {}, d.action)));
}

// ---- bath request ---------------------------------------------------------------
let bathDraft = null;   // keeps what the user typed across the 10 s refresh
function renderBath(b, dhwConfig, refresh) {
  const box = $("bath");
  if (!$("bath-title").querySelector("svg")) $("bath-title").prepend(icon("bath", { size: 20 }));
  // don't disturb typing (a focused button, e.g. the one just clicked, doesn't count)
  if (box.contains(document.activeElement) && document.activeElement.matches("input")) return;
  const live = dhwConfig && dhwConfig.enabled && dhwConfig.apply;
  if (b.request) {
    const r = b.request;
    return clear(box,
      el("div", { class: "mode" }, `Bad um ${r.time} · ${fmt(r.temp, 0, "°C")}`),
      el("ul", { class: "reasons" },
        el("li", {}, `Status: ${r.status || "wird geplant"}`),
        el("li", {}, `Aufheizen dauert ca. ${r.heat_minutes} min – spätester Start ${r.latest_start}`),
        el("li", {}, "Bis dahin wird PV-Überschuss bevorzugt, auch wenn die Batterie noch lädt.")),
      el("div", { class: "form-actions" },
        el("button", { class: "btn danger", type: "button", onclick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("/api/bath/cancel", {}); } finally { bathDraft = null; refresh(); }
        } }, icon("close", { size: 16 }), "Bad abbrechen"),
        el("span", { class: "msg" }, live
          ? "Danach gilt wieder die normale Warmwasser-Regel."
          : "Testbetrieb: es wird nichts an die Wärmepumpe gesendet.")));
  }
  const draft = bathDraft || { time: b.default_time, temp: b.default_temp };
  const timeIn = el("input", { type: "time", value: draft.time,
    oninput: (e) => { draft.time = e.target.value; bathDraft = draft; } });
  const tempIn = el("input", { type: "number", min: 40, max: 65, step: 0.5, value: draft.temp,
    oninput: (e) => { draft.temp = e.target.value; bathDraft = draft; } });
  const msg = el("span", { class: "msg" });
  clear(box,
    el("p", { class: "msg" }, "Vorher anmelden, wer baden möchte: Der Speicher wird bis zur Uhrzeit aufgeheizt, möglichst mit PV-Strom statt später aus der Batterie."),
    el("form", { class: "bath-form", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/bath", { time: timeIn.value, temp: Number(tempIn.value) });
        bathDraft = null;
        refresh();
      } catch (err) { msg.textContent = err.message; msg.className = "msg err"; }
    } },
      el("label", {}, "Badezeit", timeIn),
      el("label", {}, "Temperatur °C", tempIn),
      el("button", { class: "btn primary", type: "submit" }, "Warmwasser fürs Bad anfordern"), msg));
}

// ---- forecast -----------------------------------------------------------------------
// The factor is real yield ÷ day-ahead forecast, summed over the last days (forecaster.py).
function calibrationText(acc, factor) {
  if (!acc) return `Kalibrierung × ${fmt(factor, 2)}`;
  const y = acc.recent[0];
  const yesterday = y && y.ahead_kwh !== null
    ? ` · ${new Date(`${y.day}T12:00:00`).toLocaleDateString("de-DE", { weekday: "short" })}: Prognose ${fmt(y.ahead_kwh * factor, 1)}, real ${fmt(y.actual_kwh, 1)} kWh` : "";
  return acc.days >= acc.min_days
    ? `Kalibrierung × ${fmt(factor, 2)} (Ertrag ÷ Vortagesprognose, ${acc.days} Tage)${yesterday}`
    : `Kalibrierung × ${fmt(factor, 2)} (vorläufig; lernt aus Ertrag ÷ Vortagesprognose, ${acc.days} von ${acc.min_days} Tagen)${yesterday}`;
}

function renderForecast(f, now) {
  const box = $("forecast");
  if (!f.enabled) {
    return clear(box, el("p", { class: "msg" }, "Prognose aus. Unter „PV-Warmwasser“ Standort (Breiten-/Längengrad) und Modulflächen eintragen und „Wetterprognose nutzen“ aktivieren."));
  }
  if (!f.summary) return clear(box, el("p", { class: `msg${f.error ? " err" : ""}` }, f.error || "Prognose wird geladen …"));
  const sm = f.summary;
  const chart = el("div", { class: "chart small" });
  const days = (sm.days || []).map((d) => {
    const [, text] = weatherKind(d.code);
    return el("div", { class: "day clickable", onclick: openOutlook, title: "Nächste 3 Tage anzeigen" },
      weatherIcon(d.code, { size: 40 }),
      el("div", { class: "d-label" }, `${d.label} · ${text}`),
      el("div", { class: "d-pv" }, `${fmt(d.pv_kwh, 1)} kWh PV`),
      el("div", { class: "d-sub" }, [
        d.tmin !== null && d.tmax !== null ? `${fmt(d.tmin, 0)} bis ${fmt(d.tmax, 0)} °C` : null,
        d.sunshine_h !== null ? `${fmt(d.sunshine_h, 1)} h Sonne` : null,
        d.label === "Heute" ? `noch ${fmt(sm.remaining_today_kwh, 1)} kWh` : null,
      ].filter(Boolean).join(" · ")),
      el("div", { class: `d-parts n${(d.parts || []).length}` }, (d.parts || []).map((p) => {
        const [, desc] = weatherKind(p.code, p.night);
        return el("div", { class: "d-part", title: `${p.label} · ${p.sub}: ${desc}` },
          weatherIcon(p.code, { size: 26, night: p.night }),
          el("span", { class: "p-label" }, p.label),
          el("span", { class: "p-desc" }, `${p.sub} · ${desc}`));
      })));
  });
  clear(box,
    el("div", { class: "days" }, days),
    el("button", { class: "btn outlook-open", type: "button", onclick: openOutlook },
      "Nächste 3 Tage ", el("span", { "aria-hidden": "true" }, "›")),
    chart,
    el("p", { class: "flow-source" }, `Open-Meteo, Stand ${dayTime(sm.fetched_at)} · ${calibrationText(f.accuracy, sm.calibration)}`));
  requestAnimationFrame(() => barChart(chart, sm.hours, new Date(now)));
}

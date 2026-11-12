// Overview tab (without the time charts, see timeline.js): tiles, energy flow, the two
// control decisions, bath request, forecast and today's money.
import { locale, t } from "./i18n.js";
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
  const goal = t("web.overview.ziel", { target: fmt(target, 1, "°C") });
  if (room?.used != null) {
    return tile(t("web.overview.raum"), fmt(room.used, 1), "°C",
      [goal, room.humidity != null ? `Feuchte ${fmt(room.humidity, 0)} %` : null].filter(Boolean).join(" · "));
  }
  const why = room?.temp != null ? t("web.overview.raumfuehler_letzte_meldung_vor_min", { room: fmt(room.age_min, 0) }) : t("web.overview.kein_raumfuehler");
  return tile(t("web.overview.raum"), "–", "", `${goal} · ${why}`);
}

// The electric backup heater (1 kWh electricity = 1 kWh heat): only shown on days it ran.
function backupText(b) {
  const total = b ? (b.heating ?? 0) + (b.dhw ?? 0) : 0;
  return total >= 1 ? t("web.overview.heizstab_heute_kwh", { total: fmt(total, 0) }) : null;
}

// While the compressor runs: its measured heat output and heat ÷ motor power (FACHMANN page, every minute).
// The motor power leaves out fans and pumps, so this reads higher than the day's counter COP.
function liveCop(web) {
  if (web.cop_live == null) return null;
  return t("web.overview.jetzt_kw_waerme_cop", { web: fmt(web.heat_output_kw, 1), web2: fmt(web.cop_live, 1) });
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
    tile("PV", p(e.pv_w).v, p(e.pv_w).u, t("web.overview.heute", { ko: fmt(ko.yield_day, 1, "kWh") }), "--prod-pv"),
    tile(t("web.overview.haus"), p(e.house_w).v, p(e.house_w).u, t("web.overview.ohne_wallbox_mit_waermepumpe"), "--cons-house"),
    tile(t("web.overview.waermepumpe"), hpW === null || hpW === undefined ? "–" : p(hpW).v, hpW === null || hpW === undefined ? "" : p(hpW).u,
      hpW === null || hpW === undefined ? t("web.overview.messung_startet_2_min")
        : t("web.overview.heute_kwh_heizen_ww", { hpRuns: hpRuns ? t("web.overview.laeuft") : t("web.overview.aus"), kwh: kwh(hpToday), kwh2: kwh(web.power_heating_day), kwh3: kwh(web.power_dhw_day) }) +
          (backup >= 1 ? t("web.overview.heizstab", { kwh: kwh(backup) }) : ")"),
      "--cons-hp", liveCop(web)),
    tile(t("web.overview.batterie"), fmt(e.soc, 0), "%",
      bat == null ? null : bat < -20 ? t("web.overview.laedt", { p: p(-bat).v, p2: p(-bat).u }) : bat > 20 ? t("web.overview.entlaedt", { p: p(bat).v, p2: p(bat).u }) : t("web.overview.ruhe"),
      bat != null && bat < -20 ? "--cons-bat" : "--prod-bat"),
    tile(t("web.overview.netz"), p(Math.abs(grid ?? 0)).v, p(Math.abs(grid ?? 0)).u,
      grid == null ? null : grid > 20 ? t("web.overview.bezug") : grid < -20 ? t("web.overview.einspeisung") : t("web.overview.balanced"),
      grid != null && grid < -20 ? "--cons-grid" : "--prod-grid"),
    s.devices.openwb ? tile(t("web.overview.auto"), p(e.car_w).v, p(e.car_w).u,
      [ow.cp_mode, ow.cp_plugged === 1 ? (ow.cp_charging === 1 ? t("web.overview.laedt_2") : t("web.overview.angesteckt")) : t("web.overview.kein_fahrzeug")]
        .filter(Boolean).join(" · "), "--cons-car") : null,
  );
  clear($("tiles-heat"),
    roomTile(s.room, isg.room_hc1_target),
    tile(t("web.overview.fussbodenheizung"), fmt(isg.flow_temp, 1), "°C", t("web.overview.vorlauf_ruecklauf", { isg: fmt(isg.return_temp, 1, "°C") })),
    tile(t("web.html.warmwasser"), fmt(e.dhw_temp, 1), "°C", t("web.overview.soll", { e: fmt(e.dhw_target, 1, "°C") })),
    tile(t("web.overview.aussen"), fmt(e.outside_temp, 1), "°C", e.outside_temp !== null && e.outside_temp < (s.heating?.config?.outside_max ?? 15)
      ? t("web.overview.heizperiode") : t("web.overview.keine_heizperiode")),
    tile(t("web.overview.waerme_heute"), kwh((web.heat_heating_day ?? 0) + (web.heat_dhw_day ?? 0)), "kWh",
      t("web.overview.cop_heizen_ww", { copToday: copToday(web.heat_heating_day ?? 0, web.power_heating_day ?? 0), copToday2: copToday(web.heat_dhw_day ?? 0, web.power_dhw_day ?? 0) }),
      null, backupText(s.backup_heater)),
  );
}

// ---- the two control decisions ---------------------------------------------------
function renderDecision(c) {
  const d = c.last;
  if (!d) return clear($("decision"), el("p", { class: "msg" }, t("web.overview.erste_entscheidung_folgt_innerhalb_einer")));
  const m = d.metrics || {};
  clear($("decision"),
    el("div", {}, el("span", { class: "mode" }, c.explain.modes[d.mode] || d.mode, " → ", fmt(d.target, 1, "°C")), " ",
      d.dry_run ? el("span", { class: "badge" }, "Testbetrieb") : null),
    el("ul", { class: "reasons" }, d.reasons.map((r) => el("li", {}, deNum(r)))),
    el("dl", { class: "kv" },
      el("dt", {}, t("web.overview.aktion")), el("dd", {}, d.action),
      el("dt", {}, t("web.overview.batterie_bis_voll")), el("dd", {}, fmt(m.battery_need_kwh, 1, "kWh")),
      el("dt", {}, t("web.overview.warmwasser_bis", { m: fmt(m.boost_target, 0, "°C") })), el("dd", {}, fmt(m.boost_need_kwh, 1, t("web.overview.kwh_strom"))),
      c.power ? [el("dt", {}, t("web.overview.ww_leistung")), el("dd", {}, `${fmt(c.power.current ?? c.power.target, 0)} % – ${c.power.reason}` +
        (c.power.action && c.power.outcome !== "unchanged" ? ` (${c.power.action})` : ""))] : null,
      c.hygiene?.enabled ? [el("dt", {}, t("web.overview.legionellenschutz")), el("dd", {}, c.hygiene.overdue
        ? t("web.overview.ueberfaellig_heizt_mittags_auch_ohne_pv")
        : c.hygiene.due ? t("web.overview.faellig_wartet_auf_pv_ueberschuss")
        : t("web.overview.zuletzt", { c: new Date(c.hygiene.last).toLocaleDateString(locale(), { day: "2-digit", month: "2-digit" }) }))] : null));
}

function renderHeating(h) {
  const box = $("heating");
  if (!h) return clear(box);
  const d = h.last, c = h.config;
  if (!d) return clear(box, el("p", { class: "msg" }, t("web.overview.erste_entscheidung_folgt_innerhalb_einer")));
  const m = d.metrics || {};
  clear(box,
    el("div", {}, el("span", { class: "mode" }, h.explain.modes[d.mode] || d.mode, " → ", fmt(d.target, 1, "°C")), " ",
      d.dry_run ? el("span", { class: "badge" }, "Testbetrieb") : null),
    el("ul", { class: "reasons" }, d.reasons.map((r) => el("li", {}, deNum(r)))),
    el("dl", { class: "kv" },
      el("dt", {}, t("web.overview.aussen_raum")), el("dd", {}, `${fmt(m.outside_temp, 1, "°C")} / ${fmt(m.room_temp, 1, "°C")}`),
      el("dt", {}, "Band"), el("dd", {}, t("web.overview.c_heizperiode_unter_c", { c: fmt(c.room_min, 1), c2: fmt(c.normal_temp, 1), c3: fmt(c.room_max, 1), c4: fmt(c.outside_max, 0) })),
      el("dt", {}, t("web.overview.puffer_jetzt_bis")), el("dd", {}, `${fmt(m.boost_target, 1, "°C")} – ${deNum(m.buffer_reason || "")}`),
      el("dt", {}, t("web.overview.aktion")), el("dd", {}, d.action)));
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
      el("div", { class: "mode" }, t("web.overview.bad_um", { r: r.time, r2: fmt(r.temp, 0, "°C") })),
      el("ul", { class: "reasons" },
        el("li", {}, `Status: ${r.status || t("web.overview.wird_geplant")}`),
        el("li", {}, t("web.overview.aufheizen_dauert_ca_min_spaetester_start", { heat_minutes: r.heat_minutes, latest_start: r.latest_start })),
        el("li", {}, t("web.overview.bis_dahin_wird_pv_ueberschuss_bevorzugt"))),
      el("div", { class: "form-actions" },
        el("button", { class: "btn danger", type: "button", onclick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api("/api/bath/cancel", {}); } finally { bathDraft = null; refresh(); }
        } }, icon("close", { size: 16 }), t("web.overview.bad_abbrechen")),
        el("span", { class: "msg" }, live
          ? t("web.overview.danach_gilt_wieder_die_normale")
          : t("web.overview.testbetrieb_es_wird_nichts_an_die"))));
  }
  const draft = bathDraft || { time: b.default_time, temp: b.default_temp };
  const timeIn = el("input", { type: "time", value: draft.time,
    oninput: (e) => { draft.time = e.target.value; bathDraft = draft; } });
  const tempIn = el("input", { type: "number", min: 40, max: 65, step: 0.5, value: draft.temp,
    oninput: (e) => { draft.temp = e.target.value; bathDraft = draft; } });
  const msg = el("span", { class: "msg" });
  clear(box,
    el("p", { class: "msg" }, t("web.overview.vorher_anmelden_wer_baden_moechte_der")),
    el("form", { class: "bath-form", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/bath", { time: timeIn.value, temp: Number(tempIn.value) });
        bathDraft = null;
        refresh();
      } catch (err) { msg.textContent = err.message; msg.className = "msg err"; }
    } },
      el("label", {}, t("web.overview.badezeit"), timeIn),
      el("label", {}, t("web.overview.temperatur_c"), tempIn),
      el("button", { class: "btn primary", type: "submit" }, t("web.overview.warmwasser_fuers_bad_anfordern")), msg));
}

// ---- forecast -----------------------------------------------------------------------
// The factor is real yield ÷ day-ahead forecast, summed over the last days (forecaster.py).
function calibrationText(acc, factor) {
  if (!acc) return t("web.overview.kalibrierung", { factor: fmt(factor, 2) });
  const y = acc.recent[0];
  const yesterday = y && y.ahead_kwh !== null
    ? t("web.overview.prognose_real_kwh", { _: new Date(`${y.day}T12:00:00`).toLocaleDateString(locale(), { weekday: "short" }), y: fmt(y.ahead_kwh * factor, 1), y2: fmt(y.actual_kwh, 1) }) : "";
  return acc.days >= acc.min_days
    ? t("web.overview.kalibrierung_ertrag_vortagesprognose", { factor: fmt(factor, 2), days: acc.days, yesterday })
    : t("web.overview.kalibrierung_vorlaeufig_lernt_aus_ertrag", { factor: fmt(factor, 2), days: acc.days, min_days: acc.min_days, yesterday });
}

function renderForecast(f, now) {
  const box = $("forecast");
  if (!f.enabled) {
    return clear(box, el("p", { class: "msg" }, t("web.overview.prognose_aus_unter_pv_warmwasser")));
  }
  if (!f.summary) return clear(box, el("p", { class: `msg${f.error ? " err" : ""}` }, f.error || t("web.overview.prognose_wird_geladen")));
  const sm = f.summary;
  const chart = el("div", { class: "chart small" });
  const days = (sm.days || []).map((d, i) => {
    const [, text] = weatherKind(d.code);
    return el("div", { class: "day clickable", onclick: openOutlook, title: t("web.overview.naechste_3_tage_anzeigen") },
      weatherIcon(d.code, { size: 40 }),
      el("div", { class: "d-label" }, `${d.label} · ${text}`),
      el("div", { class: "d-pv" }, `${fmt(d.pv_kwh, 1)} kWh PV`),
      el("div", { class: "d-sub" }, [
        d.tmin !== null && d.tmax !== null ? t("web.overview.bis_c", { d: fmt(d.tmin, 0), d2: fmt(d.tmax, 0) }) : null,
        d.sunshine_h !== null ? t("web.overview.h_sonne", { d: fmt(d.sunshine_h, 1) }) : null,
        i === 0 ? t("web.overview.noch_kwh", { sm: fmt(sm.remaining_today_kwh, 1) }) : null,
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
      t("web.overview.naechste_3_tage"), el("span", { "aria-hidden": "true" }, "›")),
    chart,
    el("p", { class: "flow-source" }, t("web.overview.open_meteo_stand", { sm: dayTime(sm.fetched_at), calibrationText: calibrationText(f.accuracy, sm.calibration) })));
  requestAnimationFrame(() => barChart(chart, sm.hours, new Date(now)));
}

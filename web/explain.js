// "So funktioniert's": the PV spending strategy explained visually.
// The live parts read the trace that decide()/decide_heating() record for every decision,
// the steps and their texts that live next to those rules (hotwater.stages(), heating.stages(),
// sent as state.*.explain) and the merit order the server computes from the prices and the
// heat pump's own COPs – so this page only draws; it holds no copy of the rules.
import { locale, t } from "./i18n.js";
import { api, el, fmt, deNum } from "./lib.js";
import { icon } from "./icons.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const svgText = (attrs, s) => { const node = svgEl("text", attrs); node.textContent = s; return node; };
const ct = (eur) => `${fmt(eur * 100, 1)} ct`;

const USE_ICON = { house: "house", heating: "heatpump", dhw_daily: "heatpump", battery: "battery", dhw_extra: "heatpump", export: "grid" };

function chip(result) {
  if (result === undefined) return el("span", { class: "x-chip skip" }, t("web.explain.nicht_erreicht"));
  if (result.ok === null) return el("span", { class: "x-chip skip" }, t("web.explain.nicht_geprueft"));
  return result.ok ? el("span", { class: "x-chip yes" }, t("web.explain.ja")) : el("span", { class: "x-chip no" }, t("web.explain.nein"));
}

/**
 * Draw a decision as stages with ✓/✗ checks, highlighting the stage that decided.
 * `x` (from the server): `stages` in check order – each has `entry` (the trace node that means
 * "reached"), an optional `gate` node and `checks` ([node, question, why]) – plus `outcomes`
 * (outcome node → name) and `off` (shown while the strategy is switched off).
 */
function liveFlow(last, x) {
  const { stages: st, outcomes, off: offText } = x;
  if (!last) return el("p", { class: "msg" }, t("web.explain.die_erste_entscheidung_folgt_innerhalb"));
  const trace = last.trace || [];
  const byNode = Object.fromEntries(trace.map((r) => [r.node, r]));
  const outIdx = trace.findIndex((r) => /(^|_)out_/.test(r.node));
  const out = trace[outIdx];
  if (trace[0] && trace[0].ok === false && /enabled$/.test(trace[0].node)) return el("p", { class: "msg" }, offText);
  const reached = (s) => byNode[s.entry] !== undefined;
  // the stage that decided: the last reached stage that contains the check made just before the outcome
  const prev = outIdx > 0 ? trace[outIdx - 1].node : null;
  const decidedId = [...st].reverse().find((s) => reached(s) && (s.gate === prev || s.checks.some(([n]) => n === prev)))?.id;
  const wrap = el("div", { class: "x-flow" });
  st.forEach((s, i) => {
    const gateRes = s.gate ? byNode[s.gate] : undefined;
    const isReached = reached(s), decided = s.id === decidedId;
    wrap.append(el("section", { class: `x-stage${isReached ? " reached" : " skipped"}${decided ? " decided" : ""}` },
      el("header", {}, icon(s.icon, { size: 22 }), el("h3", {}, s.title),
        gateRes ? chip(gateRes) : null, s.and ? el("span", { class: "x-and" }, t("web.explain.alle_muessen_sein")) : null),
      el("p", { class: "x-intro" }, s.intro),
      gateRes ? el("p", { class: "x-live" }, deNum(gateRes.detail)) : null,
      el("ol", { class: "x-checks" }, s.checks.map(([node, q, why]) => {
        // inside a gated stage, checks only count when the gate said yes (node ids can repeat across stages)
        const r = isReached && (!s.gate || gateRes?.ok) ? byNode[node] : undefined;
        return el("li", { class: r ? (r.ok === null ? "skip" : r.ok ? "yes" : "no") : "unreached" },
          chip(r), el("div", {}, el("b", {}, q), el("span", { class: "x-why" }, why),
            r ? el("span", { class: "x-live" }, t("web.explain.jetzt", { deNum: deNum(r.detail) })) : null));
      })),
      s.fallback ? el("p", { class: "x-intro" }, s.fallback) : null));
    if (i < st.length - 1) {
      const flowsOn = isReached && !decided && reached(st[i + 1]);
      wrap.append(el("div", { class: `x-arrow${flowsOn ? " on" : ""}` }, "↓ ", s.gateNo || t("web.explain.nicht_alle_weiter")));
    }
  });
  wrap.append(el("div", { class: "x-result" },
    el("span", {}, t("web.explain.ergebnis_jetzt")),
    el("b", {}, `${outcomes[out?.node] || last.mode} → ${fmt(last.target, 1)} °C`),
    el("span", { class: "x-why" }, deNum(last.reasons.join(" · ")))));
  return wrap;
}

// ---- the economics: value of a PV kWh in each use (bars, one scale) -------------------------
export function copLine(eco) {
  if (!eco) return null;
  return el("p", { class: "flow-source" },
    t("web.explain.cop_heizen_jetzt", { eco: fmt(eco.cop_heating, 1), eco2: eco.cop_outside !== null && eco.cop_outside !== undefined ? t("web.explain.bei_c_aussen", { eco: fmt(eco.cop_outside, 1) }) : "" }) +
    t("web.explain.lwz_5_s_plus_datenblatt_kurve", { eco: fmt(eco.cop_scale, 2), eco2: fmt(eco.cop_dhw, 2) }) +
    t("web.explain.cop_vorteil_am_tag", { eco: fmt(eco.day_cop_gain * 100, 0), eco2: !eco.day_gain_from_forecast ? t("web.explain.standardwert")
      : eco.day_gain_measured ? t("web.explain.prognose_eigene_messung") : t("web.explain.prognose_datenblatt") }) +
    (eco.cop_live ? t("web.explain.gemessen_cop_je_k_h_heizen", { eco: fmt(eco.cop_live.per_k, 2), eco2: fmt(eco.cop_live.minutes / 60, 0) }) +
      `${fmt(eco.cop_live.range[0], 0)}…${fmt(eco.cop_live.range[1], 0)} °C · ` : "") +
    `Kurve: ${eco.cop_curve.map((p) => `${p.t} °C → ${fmt(p.cop, 1)}`).join(", ")}`);
}

export function meritBars(eco) {
  if (!eco) return el("p", { class: "msg" }, t("web.html.laedt"));
  const max = Math.max(...eco.merit_order.map((u) => u.value));
  const feed = eco.prices.feed;
  return el("div", { class: "x-merit", role: "list" }, eco.merit_order.map((u, i) =>
    el("div", { class: `x-merit-row${u.key === "export" ? " export" : ""}`, role: "listitem" },
      el("span", { class: "x-rank" }, `${i + 1}.`),
      icon(USE_ICON[u.key] || "pv", { size: 18 }),
      el("div", { class: "x-merit-label" }, el("b", {}, u.label), el("span", {}, u.note)),
      el("div", { class: "x-merit-track" },
        el("div", { class: "x-merit-bar", style: `width:${(100 * u.value / max).toFixed(1)}%` }),
        el("div", { class: "x-merit-feed", style: `left:${(100 * feed / max).toFixed(1)}%`, title: t("web.explain.einspeiseverguetung") })),
      el("span", { class: "x-merit-val" }, `${ct(u.value)}/kWh`,
        u.key === "export" ? null : el("small", {}, t("web.explain.ggue_einspeisen", { ct: ct(u.gain_vs_feed) }))))));
}

// ---- schematic days: what the targets do over 24 h -------------------------------------------
function dayChart(title, segs, unitRange, pvPeak, note) {
  const W = 520, H = 200, m = { l: 40, r: 12, t: 26, b: 26 };
  const [tMin, tMax] = unitRange;
  const X = (h) => m.l + (h / 24) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - tMin) / (tMax - tMin)) * (H - m.t - m.b);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "x-day", role: "img", "aria-label": `${title}: ${note}` });
  svg.append(svgText({ x: m.l, y: 15, class: "x-day-title" }, title));
  const pts = [];
  for (let h = 7; h <= 17; h += 0.25) pts.push(`${X(h)},${Y(tMin) - Math.max(0, Math.sin(((h - 7) / 10) * Math.PI)) * pvPeak * (H - m.t - m.b) * 0.5}`);
  svg.append(svgEl("polygon", { points: `${X(7)},${Y(tMin)} ${pts.join(" ")} ${X(17)},${Y(tMin)}`, class: "x-pv" }));
  const step = (tMax - tMin) / 4;
  for (let v = tMin; v <= tMax + 0.01; v += step) {
    svg.append(svgEl("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), class: "gridline" }));
    svg.append(svgText({ x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", class: "x-axis" }, `${fmt(v, step < 1 ? 1 : 0)}°`));
  }
  for (const h of [0, 6, 12, 18, 24]) svg.append(svgText({ x: X(h), y: H - 8, "text-anchor": "middle", class: "x-axis" }, `${h}:00`));
  let d = "";
  segs.forEach(([a, b, v], i) => { d += `${i ? "L" : "M"}${X(a)},${Y(v)} L${X(b)},${Y(v)} `; });
  svg.append(svgEl("path", { d, class: "x-target" }));
  segs.forEach(([a, b, v, label]) => {
    if (label) svg.append(svgText({ x: (X(a) + X(b)) / 2, y: Y(v) - 7, "text-anchor": "middle", class: "x-seg" }, label));
  });
  return svg;
}

function typicalDays(c, h) {
  const lo = h.room_min, hi = h.room_max;
  const winter = dayChart(t("web.explain.wintertag_raumziel_hk1_fussbodenheizung"),
    [[0, 9.5, lo, t("web.explain.absenkung_waerme_vom_vortag")], [9.5, 15, hi, t("web.explain.pv_heizt_vor")], [15, 24, lo, t("web.explain.absenkung_bis_morgen")]],
    [lo - 0.5, hi + 0.5], 0.9, t("web.explain.vorheizen_nur_bei_sonne_nachts_traegt"));
  const summer = dayChart("Sommertag: Warmwasserziel",
    [[0, 10, c.min_temp, t("web.explain.warten_auf_sonne")], [10, 12, c.comfort_temp, t("web.explain.tagesbedarf_mit_pv")],
     [12, 24, c.min_temp, t("web.explain.warten_auf_morgen")]],
    [c.min_temp - 4, c.boost_temp + 2], 1, t("web.explain.tagesbedarf_mittags_mit_pv_kein"));
  return el("div", { class: "x-days" }, winter, summer);
}

function knobs(c, h, eco) {
  const rows = [
    [t("web.explain.strompreis_einspeisung"), `${ct(eco?.prices.buy ?? 0)} / ${ct(eco?.prices.feed ?? 0)}`, t("web.explain.grundlage_der_reihenfolge")],
    [t("web.explain.cop_heizen_warmwasser"), `${fmt(eco?.cop_heating, 1)} / ${fmt(eco?.cop_dhw, 1)}`, t("web.explain.aus_den_zaehlern_der_waermepumpe_deshalb")],
    [t("web.explain.raum_minimum_normal_puffer_maximum"), `${fmt(h.room_min, 1)} / ${fmt(h.normal_temp, 1)} / ${fmt(h.room_max, 1)} °C`, t("web.explain.komfortband_der_fussbodenheizung_puffer")],
    [t("web.explain.batterie_gilt_als_voll_ab"), `${fmt(h.soc_full, 0)} %`, t("web.explain.bis_dahin_geht_die_batterie_vor")],
    [t("web.explain.warmwasser_minimum_tagesbedarf_vorrat"), `${fmt(c.min_temp, 0)} / ${fmt(c.comfort_temp, 0)} / ${fmt(c.boost_temp, 0)} °C`, t("web.explain.vorrat_nur_vor_einem_trueben_tag")],
    [t("web.explain.trueber_tag_unter"), `${fmt(c.poor_day_kwh, 1)} kWh`, t("web.explain.entscheidet_ueber_vorrat_und_absenkung")],
  ];
  return el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Einstellung"), el("th", { class: "num" }, t("web.explain.wert")), el("th", {}, "Wirkung"))),
    el("tbody", {}, rows.map(([a, b, w]) => el("tr", {}, el("td", {}, a), el("td", { class: "num" }, b), el("td", {}, w)))));
}

let simCache = null, simLoading = false;
export function simulationCard() {
  const box = el("div", {}, el("p", { class: "msg" }, t("web.explain.simulation_laeuft")));
  const intro = el("p", { class: "x-intro" }, t("web.explain.dieselben_regeln_wie_unter_so") +
    t("web.explain.kfw_40_20_kwh_k_speicher") +
    t("web.explain.raum_normal_warmwasser_komfort_kosten"));
  const draw = (rows) => box.replaceChildren(intro, el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Modelltag", t("web.explain.cop_heizen"), t("web.explain.feste_werte"), "Strategie", "Ersparnis", "WP nachts", t("web.explain.netzbezug"), t("web.overview.raum")].map((h, i) =>
      el("th", { class: i ? "num" : "" }, h)))),
    el("tbody", {}, rows.map((r) => el("tr", {},
      el("td", {}, r.scenario),
      el("td", { class: "num" }, fmt(r.cop_heating, 1)),
      el("td", { class: "num" }, `${fmt(r.baseline_eur, 2)} €`),
      el("td", { class: "num" }, `${fmt(r.strategy_eur, 2)} €`),
      el("td", { class: "num" }, el("b", {}, `${fmt(r.saving_eur, 2)} €`)),
      el("td", { class: "num" }, `${fmt(r.hp_night_kwh[0], 1)} → ${fmt(r.hp_night_kwh[1], 1)} kWh`),
      el("td", { class: "num" }, `${fmt(r.import_kwh[0], 1)} → ${fmt(r.import_kwh[1], 1)} kWh`),
      el("td", { class: "num" }, `${fmt(r.room_range[0], 1)}–${fmt(r.room_range[1], 1)} °C`)))))));
  if (simCache) draw(simCache);
  else if (!simLoading) {
    simLoading = true;
    api("/api/simulation").then((rows) => { simCache = rows; draw(rows); })
      .catch((e) => box.replaceChildren(el("p", { class: "msg err" }, e.message)))
      .finally(() => { simLoading = false; });
  }
  return box;
}

export function renderExplain(root, state) {
  if (root.contains(document.activeElement)) return;
  const c = state.controller.config, h = state.heating?.config, eco = state.economy;
  const stamp = (d) => (d ? t("web.explain.stand", { d: new Date(d.time).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" }) }) : "");
  root.replaceChildren(
    el("article", { class: "card" }, el("h2", {}, t("web.explain.die_rechnung_was_eine_kwh_sonnenstrom")),
      el("p", { class: "x-intro" }, t("web.explain.netzstrom_kostet_einspeisen_bringt", { ct: ct(eco?.prices.buy ?? 0), ct2: ct(eco?.prices.feed ?? 0) }) +
        t("web.explain.jede_kwh_die_eine_spaetere_kwh") +
        t("web.explain.gespeicherte_waerme_zaehlt_aber_nur_wenn")),
      meritBars(eco), copLine(eco),
      el("p", { class: "x-intro" }, t("web.explain.was_das_in_euro_bringt_zeigt") +
        t("web.explain.im_winter_heizt_die_regelung_trotzdem") +
        t("web.explain.darum_gibt_es_keinen_55_c") +
        t("web.explain.im_winter_ist_das_haus_selbst"))),
    h ? el("article", { class: "card" }, el("h2", {}, t("web.explain.1_heizen_zuerst")),
      el("p", { class: "x-intro" }, t("web.explain.jede_minute_zuerst_soll_das_haus") +
        stamp(state.heating.last) + (h.apply ? "" : t("web.explain.testbetrieb_es_wird_nur_angezeigt"))),
      liveFlow(state.heating.last, state.heating.explain)) : null,
    el("article", { class: "card" }, el("h2", {}, t("web.explain.2_warmwasser")),
      el("p", { class: "x-intro" }, t("web.explain.danach_das_warmwasser_tagesbedarf_mit_pv") + stamp(state.controller.last)),
      liveFlow(state.controller.last, state.controller.explain)),
    h ? el("article", { class: "card" }, el("h2", {}, t("web.explain.ein_typischer_tag")),
      el("p", { class: "x-intro" }, t("web.explain.schematisch_linie_ziel_der_regelung")),
      typicalDays(c, h)) : null,
    el("article", { class: "card" }, el("h2", {}, t("web.explain.die_wichtigsten_stellschrauben")),
      el("div", { class: "table-wrap" }, h ? knobs(c, h, eco) : null),
      el("p", { class: "x-intro" }, t("web.explain.aendern_unter_pv_regelung_jede_regelung"))));
}

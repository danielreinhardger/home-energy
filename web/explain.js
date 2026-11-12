// "So funktioniert's": the PV spending strategy explained visually.
// The live parts read the trace that decide()/decide_heating() record for every decision,
// the steps and their texts that live next to those rules (hotwater.stages(), heating.stages(),
// sent as state.*.explain) and the merit order the server computes from the prices and the
// heat pump's own COPs – so this page only draws; it holds no copy of the rules.
import { api, el, fmt, deNum } from "./lib.js";
import { icon } from "./icons.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const svgText = (attrs, s) => { const t = svgEl("text", attrs); t.textContent = s; return t; };
const ct = (eur) => `${fmt(eur * 100, 1)} ct`;

const USE_ICON = { house: "house", heating: "heatpump", dhw_daily: "heatpump", battery: "battery", dhw_extra: "heatpump", export: "grid" };

function chip(result) {
  if (result === undefined) return el("span", { class: "x-chip skip" }, "– nicht erreicht");
  if (result.ok === null) return el("span", { class: "x-chip skip" }, "– nicht geprüft");
  return result.ok ? el("span", { class: "x-chip yes" }, "✓ ja") : el("span", { class: "x-chip no" }, "✗ nein");
}

/**
 * Draw a decision as stages with ✓/✗ checks, highlighting the stage that decided.
 * `x` (from the server): `stages` in check order – each has `entry` (the trace node that means
 * "reached"), an optional `gate` node and `checks` ([node, question, why]) – plus `outcomes`
 * (outcome node → name) and `off` (shown while the strategy is switched off).
 */
function liveFlow(last, x) {
  const { stages: st, outcomes, off: offText } = x;
  if (!last) return el("p", { class: "msg" }, "Die erste Entscheidung folgt innerhalb einer Minute.");
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
        gateRes ? chip(gateRes) : null, s.and ? el("span", { class: "x-and" }, "alle müssen ✓ sein") : null),
      el("p", { class: "x-intro" }, s.intro),
      gateRes ? el("p", { class: "x-live" }, deNum(gateRes.detail)) : null,
      el("ol", { class: "x-checks" }, s.checks.map(([node, q, why]) => {
        // inside a gated stage, checks only count when the gate said yes (node ids can repeat across stages)
        const r = isReached && (!s.gate || gateRes?.ok) ? byNode[node] : undefined;
        return el("li", { class: r ? (r.ok === null ? "skip" : r.ok ? "yes" : "no") : "unreached" },
          chip(r), el("div", {}, el("b", {}, q), el("span", { class: "x-why" }, why),
            r ? el("span", { class: "x-live" }, `Jetzt: ${deNum(r.detail)}`) : null));
      })),
      s.fallback ? el("p", { class: "x-intro" }, s.fallback) : null));
    if (i < st.length - 1) {
      const flowsOn = isReached && !decided && reached(st[i + 1]);
      wrap.append(el("div", { class: `x-arrow${flowsOn ? " on" : ""}` }, "↓ ", s.gateNo || "nicht alle ✓ → weiter"));
    }
  });
  wrap.append(el("div", { class: "x-result" },
    el("span", {}, "Ergebnis jetzt"),
    el("b", {}, `${outcomes[out?.node] || last.mode} → ${fmt(last.target, 1)} °C`),
    el("span", { class: "x-why" }, deNum(last.reasons.join(" · ")))));
  return wrap;
}

// ---- the economics: value of a PV kWh in each use (bars, one scale) -------------------------
export function copLine(eco) {
  if (!eco) return null;
  return el("p", { class: "flow-source" },
    `COP Heizen jetzt ${fmt(eco.cop_heating, 1)}${eco.cop_outside !== null && eco.cop_outside !== undefined ? ` bei ${fmt(eco.cop_outside, 1)} °C außen` : ""} ` +
    `(LWZ 5 S Plus: Datenblatt-Kurve × ${fmt(eco.cop_scale, 2)} nach den eigenen Zählern) · Warmwasser ${fmt(eco.cop_dhw, 2)} · ` +
    `COP-Vorteil am Tag ${fmt(eco.day_cop_gain * 100, 0)} %${!eco.day_gain_from_forecast ? " (Standardwert)"
      : eco.day_gain_measured ? " (Prognose + eigene Messung)" : " (Prognose + Datenblatt)"} · ` +
    (eco.cop_live ? `gemessen: ${fmt(eco.cop_live.per_k, 2)} COP je K, ${fmt(eco.cop_live.minutes / 60, 0)} h Heizen bei ` +
      `${fmt(eco.cop_live.range[0], 0)}…${fmt(eco.cop_live.range[1], 0)} °C · ` : "") +
    `Kurve: ${eco.cop_curve.map((p) => `${p.t} °C → ${fmt(p.cop, 1)}`).join(", ")}`);
}

export function meritBars(eco) {
  if (!eco) return el("p", { class: "msg" }, "Lädt …");
  const max = Math.max(...eco.merit_order.map((u) => u.value));
  const feed = eco.prices.feed;
  return el("div", { class: "x-merit", role: "list" }, eco.merit_order.map((u, i) =>
    el("div", { class: `x-merit-row${u.key === "export" ? " export" : ""}`, role: "listitem" },
      el("span", { class: "x-rank" }, `${i + 1}.`),
      icon(USE_ICON[u.key] || "pv", { size: 18 }),
      el("div", { class: "x-merit-label" }, el("b", {}, u.label), el("span", {}, u.note)),
      el("div", { class: "x-merit-track" },
        el("div", { class: "x-merit-bar", style: `width:${(100 * u.value / max).toFixed(1)}%` }),
        el("div", { class: "x-merit-feed", style: `left:${(100 * feed / max).toFixed(1)}%`, title: "Einspeisevergütung" })),
      el("span", { class: "x-merit-val" }, `${ct(u.value)}/kWh`,
        u.key === "export" ? null : el("small", {}, ` +${ct(u.gain_vs_feed)} ggü. Einspeisen`)))));
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
  const winter = dayChart("Wintertag: Raumziel HK1 (Fußbodenheizung)",
    [[0, 9.5, lo, "Absenkung – Wärme vom Vortag"], [9.5, 15, hi, "PV heizt vor"], [15, 24, lo, "Absenkung bis morgen"]],
    [lo - 0.5, hi + 0.5], 0.9, "Vorheizen nur bei Sonne, nachts trägt die gespeicherte Wärme");
  const summer = dayChart("Sommertag: Warmwasserziel",
    [[0, 10, c.min_temp, "Warten auf Sonne"], [10, 12, c.comfort_temp, "Tagesbedarf mit PV"],
     [12, 24, c.min_temp, "Warten auf morgen"]],
    [c.min_temp - 4, c.boost_temp + 2], 1, "Tagesbedarf mittags mit PV, kein Überheizen");
  return el("div", { class: "x-days" }, winter, summer);
}

function knobs(c, h, eco) {
  const rows = [
    ["Strompreis / Einspeisung", `${ct(eco?.prices.buy ?? 0)} / ${ct(eco?.prices.feed ?? 0)}`, "Grundlage der Reihenfolge."],
    ["COP Heizen / Warmwasser", `${fmt(eco?.cop_heating, 1)} / ${fmt(eco?.cop_dhw, 1)}`, "Aus den Zählern der Wärmepumpe – deshalb geht im Winter das Haus vor."],
    ["Raum Minimum / normal / Puffer-Maximum", `${fmt(h.room_min, 1)} / ${fmt(h.normal_temp, 1)} / ${fmt(h.room_max, 1)} °C`, "Komfortband der Fußbodenheizung; Puffer je nach Sonne."],
    ["Batterie gilt als voll ab", `${fmt(h.soc_full, 0)} %`, "Bis dahin geht die Batterie vor; das Haus heizt nur vor, wenn sie heute sicher voll wird."],
    ["Warmwasser Minimum / Tagesbedarf / Vorrat", `${fmt(c.min_temp, 0)} / ${fmt(c.comfort_temp, 0)} / ${fmt(c.boost_temp, 0)} °C`, "Vorrat nur vor einem trüben Tag."],
    ["Trüber Tag unter", `${fmt(c.poor_day_kwh, 1)} kWh`, "Entscheidet über Vorrat und Absenkung."],
  ];
  return el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Einstellung"), el("th", { class: "num" }, "Wert"), el("th", {}, "Wirkung"))),
    el("tbody", {}, rows.map(([a, b, w]) => el("tr", {}, el("td", {}, a), el("td", { class: "num" }, b), el("td", {}, w)))));
}

let simCache = null, simLoading = false;
export function simulationCard() {
  const box = el("div", {}, el("p", { class: "msg" }, "Simulation läuft …"));
  const intro = el("p", { class: "x-intro" }, "Dieselben Regeln wie unter „So funktioniert's“, minütlich gegen ein einfaches Modell von Haus " +
    "(KfW 40+, 20 kWh/K Speicher), Warmwasserspeicher, Batterie und PV gerechnet – verglichen mit festen Werten " +
    "(Raum normal, Warmwasser Komfort). Kosten pro Tag: Netzbezug × Strompreis − Einspeisung × Vergütung. Modellwerte, keine Messung.");
  const draw = (rows) => box.replaceChildren(intro, el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Modelltag", "COP Heizen", "Feste Werte", "Strategie", "Ersparnis", "WP nachts", "Netzbezug", "Raum"].map((h, i) =>
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
  const stamp = (d) => (d ? ` Stand ${new Date(d.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}.` : "");
  root.replaceChildren(
    el("article", { class: "card" }, el("h2", {}, "Die Rechnung: was eine kWh Sonnenstrom wert ist"),
      el("p", { class: "x-intro" }, `Netzstrom kostet ${ct(eco?.prices.buy ?? 0)}, Einspeisen bringt ${ct(eco?.prices.feed ?? 0)}. ` +
        "Jede kWh, die eine spätere kWh aus Netz oder Batterie ersetzt, ist also fast viermal so viel wert wie eingespeist. " +
        "Gespeicherte Wärme zählt aber nur, wenn sie vor der nächsten Sonne gebraucht wird – sonst verdrängt sie nur morgigen Gratis-Strom."),
      meritBars(eco), copLine(eco),
      el("p", { class: "x-intro" }, "Was das in Euro bringt, zeigt der Tab „Finanzen“. Die Plätze 2–4 liegen fast gleichauf: Sie verlegen Wärme bzw. Strom, der ohnehin gebraucht wird, in die Sonnenstunden. " +
        "Im Winter heizt die Regelung trotzdem zuerst das Haus vor – dort passt mit Abstand am meisten hinein (mehrere kWh Strom pro Tag statt ~1 kWh Warmwasser). " +
        "Darum gibt es keinen 55-°C-Boost mehr: Warmwasser über den Bedarf hat den schlechtesten COP und lohnt nur vor einem trüben Tag. " +
        "Im Winter ist das Haus selbst der beste Speicher: Estrich und Bodenplatte nehmen die Wärme mit COP ~4,6 auf, das KfW-40+-Haus verliert über Nacht nur etwa 1–1,5 K.")),
    h ? el("article", { class: "card" }, el("h2", {}, "1 · Heizen (zuerst)"),
      el("p", { class: "x-intro" }, "Jede Minute zuerst: Soll das Haus mit PV vorgeheizt werden – oder darf es ohne PV etwas abkühlen?" +
        stamp(state.heating.last) + (h.apply ? "" : " Testbetrieb: es wird nur angezeigt.")),
      liveFlow(state.heating.last, state.heating.explain)) : null,
    el("article", { class: "card" }, el("h2", {}, "2 · Warmwasser"),
      el("p", { class: "x-intro" }, "Danach das Warmwasser: Tagesbedarf mit PV, Vorrat nur vor einem trüben Tag." + stamp(state.controller.last)),
      liveFlow(state.controller.last, state.controller.explain)),
    h ? el("article", { class: "card" }, el("h2", {}, "Ein typischer Tag"),
      el("p", { class: "x-intro" }, "Schematisch: Linie = Ziel der Regelung, Fläche = PV-Erzeugung. Die Batterie bleibt für die Haushaltsgeräte am Abend."),
      typicalDays(c, h)) : null,
    el("article", { class: "card" }, el("h2", {}, "Die wichtigsten Stellschrauben"),
      el("div", { class: "table-wrap" }, h ? knobs(c, h, eco) : null),
      el("p", { class: "x-intro" }, "Ändern unter „PV-Regelung“. Jede Regelung schreibt höchstens alle 10–15 min und begrenzt pro Tag; beim Ausschalten wird der Normalwert zurückgeschrieben.")));
}

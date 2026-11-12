// Mirrored area chart: producers above the zero line, consumers below. One y-axis, in watts.
// Stacked: series on top of each other, outwards from the zero line (PV and house next to it).
// The outer edge is the total, and because production = consumption at every minute,
// both sides mirror each other. Consumers are negative (axis and tooltip show a minus).
import { t } from "./i18n.js";
import { el, fmt, power, dayTime, dateTime } from "./lib.js";
import { showTip, hideTip, niceTicks, timeTicks } from "./charts.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const pos = (v) => (typeof v === "number" && v > 0 ? v : 0);
const GAP_MS = 5 * 60 * 1000;          // longer gaps (server down) break the areas

// Distinct hues within each side (validated all-pairs, style.css); position (above/below zero)
// separates producers from consumers. Car and feed-in are dashed as a second cue.
const hp = (d) => Math.min(pos(d.hp_w), pos(d.house_w));            // heat pump is part of the house load
const PRODUCERS = () => [
  { key: "pv", label: "PV", color: "--prod-pv", get: (d) => pos(d.pv_w) },
  { key: "bat_out", label: t("web.balancechart.batterie_entlaedt"), color: "--prod-bat", get: (d) => pos(d.battery_w) },
  { key: "grid_in", label: t("web.explain.netzbezug"), color: "--prod-grid", get: (d) => pos(d.grid_w) },
];
const CONSUMERS = () => [
  { key: "house", label: t("web.balancechart.haus_ohne_wp"), color: "--cons-house", get: (d) => pos(d.house_w) - hp(d) },
  { key: "hp", label: t("web.overview.waermepumpe"), color: "--cons-hp", get: hp },
  { key: "car", label: t("web.overview.auto"), color: "--cons-car", dashed: true, get: (d) => pos(d.car_w) },
  { key: "bat_in", label: t("web.balancechart.batterie_laedt"), color: "--cons-bat", get: (d) => pos(-(d.battery_w ?? 0)) },
  { key: "grid_out", label: t("web.overview.einspeisung"), color: "--cons-grid", dashed: true, get: (d) => pos(-(d.grid_w ?? 0)) },
];

const fmtW = (w) => { const p = power(w); return `${p.v} ${p.u}`; };
const neg = (w) => `−${fmtW(w)}`;          // consumers are below zero: typographic minus

export function balanceLegend(container) {
  const item = (s) => el("span", {}, el("i", { class: `box${s.dashed ? " dashed" : ""}`,
    style: `background:var(${s.color});color:var(${s.color})` }), s.label);
  container.replaceChildren(
    el("div", { class: "legend-row" }, el("b", {}, t("web.balancechart.erzeugung")), PRODUCERS().map(item)),
    el("div", { class: "legend-row" }, el("b", {}, t("web.balancechart.verbrauch")), CONSUMERS().map(item)));
}

export function balanceChart(container, data) {
  container.replaceChildren();
  if (data.length < 2) {
    container.append(el("div", { class: "empty" }, t("web.balancechart.noch_zu_wenig_verlauf_es_kommt")));
    return;
  }
  const W = container.clientWidth || 600, H = container.clientHeight || 300;
  const m = { l: 52, r: 12, t: 10, b: 22 };
  const xs = data.map((d) => new Date(d.t).getTime());
  const up = data.map((d) => PRODUCERS().map((s) => s.get(d)));
  const down = data.map((d) => CONSUMERS().map((s) => s.get(d)));
  const sum = (r) => r.reduce((a, b) => a + b, 0);
  const maxUp = Math.max(1, ...up.map(sum));
  const maxDown = Math.max(1, ...down.map(sum));
  const ticks = niceTicks(-maxDown, maxUp, H < 220 ? 4 : 6);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const x0 = xs[0], x1 = xs[xs.length - 1];
  const X = (t) => m.l + ((t - x0) / Math.max(1, x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": t("web.balancechart.erzeugung_oberhalb_verbrauch_unterhalb") });
  const axis = svgEl("g", { class: "axis" });
  for (const v of ticks) {
    axis.append(svgEl("line", { class: v === 0 ? "zero" : "gridline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const label = svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" });
    label.textContent = v === 0 ? "0" : `${v < 0 ? "−" : ""}${fmt(Math.abs(v) / 1000, Math.abs(y1 - y0) < 4000 ? 1 : 0)} kW`;
    axis.append(label);
  }
  for (const { t, label } of timeTicks(x0, x1)) {
    if (t <= x0) continue;
    const text = svgEl("text", { x: X(t), y: H - 4, "text-anchor": "middle" });
    text.textContent = label;
    axis.append(text);
  }
  svg.append(axis);

  // contiguous runs of samples (a gap > 5 min ends a run)
  // (thinned data has wider spacing, so the gap scales with the typical step)
  const gap = Math.max(GAP_MS, 3 * (x1 - x0) / Math.max(1, xs.length - 1));
  const runs = [];
  let start = 0;
  for (let i = 1; i <= xs.length; i++) {
    if (i === xs.length || xs[i] - xs[i - 1] > gap) { if (i - start >= 2) runs.push([start, i]); start = i; }
  }
  const stack = (rows, series, sign) => {
    const below = rows.map(() => 0);
    series.forEach((s, k) => {
      if (Math.max(0, ...rows.map((r) => r[k])) < 1) return;
      const dash = s.dashed ? " dashed" : "";
      for (const [a, b] of runs) {
        const top = [], bottom = [];
        for (let i = a; i < b; i++) {
          top.push(`${X(xs[i]).toFixed(1)},${Y(sign * (below[i] + rows[i][k])).toFixed(1)}`);
          bottom.push(`${X(xs[i]).toFixed(1)},${Y(sign * below[i]).toFixed(1)}`);
        }
        svg.append(svgEl("polygon", { class: `area${dash}`, points: `${top.join(" ")} ${bottom.reverse().join(" ")}`,
          fill: `var(${s.color})` }));
        svg.append(svgEl("polyline", { class: `area-line${dash}`, points: top.join(" "), stroke: `var(${s.color})` }));
      }
      rows.forEach((r, i) => { below[i] += r[k]; });
    });
  };
  stack(up, PRODUCERS(), 1);
  stack(down, CONSUMERS(), -1);

  const tag = (txt, y, baseline) => {
    const label = svgEl("text", { class: "side-label", x: m.l + 8, y, "dominant-baseline": baseline });
    label.textContent = txt;
    svg.append(label);
  };
  tag(t("web.balancechart.erzeugung"), m.t + 4, "hanging");
  tag(t("web.balancechart.verbrauch"), H - m.b - 6, "auto");

  // hover: crosshair + tooltip with every producer and consumer at that minute
  const cross = svgEl("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const hit = svgEl("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent", tabindex: 0 });
  svg.append(cross, hit);
  const at = (i, evt) => {
    const x = X(xs[i]);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    const rows = [];
    PRODUCERS().forEach((s, k) => { if (up[i][k] >= 1) rows.push({ color: `var(${s.color})`, label: `▲ ${s.label}`, value: fmtW(up[i][k]) }); });
    CONSUMERS().forEach((s, k) => { if (down[i][k] >= 1) rows.push({ color: `var(${s.color})`, label: `▼ ${s.label}`, value: neg(down[i][k]) }); });
    if (rows.length) {
      rows.unshift({ color: "transparent", label: t("web.balancechart.summe"), value: `${fmtW(sum(up[i]))} / ${neg(sum(down[i]))}` });
    }
    showTip(evt, (x1 - x0 > 50 * 3.6e6 ? dateTime : dayTime)(data[i].t), rows.length ? rows : [{ color: "transparent", label: t("web.balancechart.keine_leistung"), value: "" }]);
  };
  const nearest = (clientX) => {
    const r = svg.getBoundingClientRect();
    const at = x0 + ((clientX - r.left) * (W / r.width) - m.l) / (W - m.l - m.r) * (x1 - x0);
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(x - at) < Math.abs(xs[best] - at)) best = i; });
    return best;
  };
  let focus = data.length - 1;
  hit.addEventListener("pointermove", (e) => at(nearest(e.clientX), e));
  hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  hit.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    focus = Math.max(0, Math.min(data.length - 1, focus + (e.key === "ArrowLeft" ? -1 : 1)));
    const r = hit.getBoundingClientRect();
    at(focus, { clientX: r.left + (X(xs[focus]) - m.l) * (r.width / (W - m.l - m.r)), clientY: r.top + 20 });
  });
  hit.addEventListener("blur", hideTip);
  container.append(svg);
}

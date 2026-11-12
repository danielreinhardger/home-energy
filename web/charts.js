// Small SVG charts: multi-series line chart with crosshair tooltip, and a bar chart.
// One y-axis per chart; series colors come from CSS variables (--series-N).
import { el, fmt, time, dayTime, dateTime } from "./lib.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const tooltip = () => document.getElementById("tooltip");

/**
 * Time-axis labels for any span: hours for up to two days, then days, weeks (Mondays)
 * and months. Returns [{ t, label }] between x0 and x1 (epoch ms).
 */
export function timeTicks(x0, x1) {
  const spanH = (x1 - x0) / 3.6e6, out = [];
  const wd = (d) => d.toLocaleDateString("de-DE", { weekday: "short" });
  if (spanH <= 50) {
    const stepH = spanH <= 4 ? 1 : spanH <= 13 ? 2 : 6;
    const d = new Date(x0); d.setMinutes(0, 0, 0);
    for (; d.getTime() <= x1; d.setHours(d.getHours() + 1)) {
      if (d.getTime() < x0 || d.getHours() % stepH) continue;
      out.push({ t: d.getTime(), label: d.getHours() === 0 ? wd(d) : `${d.getHours()}:00` });
    }
    return out;
  }
  const d = new Date(x0); d.setHours(0, 0, 0, 0);
  if (spanH <= 24 * 10) {                                          // days
    for (d.setDate(d.getDate() + 1); d.getTime() <= x1; d.setDate(d.getDate() + 1))
      out.push({ t: d.getTime(), label: `${wd(d)} ${d.getDate()}.` });
  } else if (spanH <= 24 * 70) {                                   // weeks, on Mondays
    d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
    for (; d.getTime() <= x1; d.setDate(d.getDate() + 7))
      out.push({ t: d.getTime(), label: `${d.getDate()}.${d.getMonth() + 1}.` });
  } else {                                                         // months
    d.setDate(1); d.setMonth(d.getMonth() + 1);
    const every = spanH > 24 * 400 ? 3 : 1;
    for (; d.getTime() <= x1; d.setMonth(d.getMonth() + 1)) {
      if (d.getMonth() % every) continue;
      const m = d.toLocaleDateString("de-DE", { month: "short" });
      out.push({ t: d.getTime(), label: d.getMonth() === 0 ? `${m} ${String(d.getFullYear()).slice(2)}` : m });
    }
  }
  return out;
}

export function niceTicks(min, max, count = 5) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

export function showTip(evt, title, rows) {
  const tip = tooltip();
  tip.replaceChildren(
    el("div", { class: "t" }, title),
    ...rows.map((r) => el("div", { class: "row" },
      el("i", { style: `background:${r.color}` }), r.label, el("b", {}, r.value))),
  );
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
export const hideTip = () => { tooltip().hidden = true; };

/**
 * series: [{key, label, color (css var name), unit, digits, dashed}]
 * data:   [{t: iso, [key]: number|null}]
 */
export function lineChart(container, data, series, { unit = "", digits = 0, yMin, yMax, endLabels = true, format,
  refLines = [] } = {}) {
  container.replaceChildren();
  if (!data.length) {
    container.append(el("div", { class: "empty" }, "Noch keine Daten – Verlauf füllt sich minütlich."));
    return;
  }
  const W = container.clientWidth || 600, H = container.clientHeight || 260;
  const m = { l: 48, r: endLabels ? 74 : 12, t: 8, b: 22 };
  const xs = data.map((d) => new Date(d.t).getTime());
  const vals = series.flatMap((s) => data.map((d) => d[s.key])).filter((v) => v !== null && v !== undefined);
  const lo = Math.min(yMin ?? Infinity, ...vals), hi = Math.max(yMax ?? -Infinity, ...vals);
  const ticks = niceTicks(lo, hi, H < 200 ? 3 : 5);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const x0 = xs[0], x1 = Math.max(xs[xs.length - 1], x0 + 60000);
  const X = (t) => m.l + ((t - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const f = format || ((v) => fmt(v, digits, unit));

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((s) => s.label).join(", ") });
  const axis = svgEl("g", { class: "axis" });
  for (const v of ticks) {
    axis.append(svgEl("line", { class: v === 0 && y0 < 0 ? "zero" : "gridline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const t = svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" });
    t.textContent = fmt(v, Math.abs(y1 - y0) < 5 ? 1 : 0);
    axis.append(t);
  }
  for (const { t, label } of timeTicks(x0, x1)) {
    const text = svgEl("text", { x: X(t), y: H - 4, "text-anchor": "middle" });
    text.textContent = label;
    axis.append(text);
  }
  svg.append(axis);
  // reference lines (e.g. the battery's discharge limit): dotted, labelled, behind the data
  for (const r of refLines) {
    if (r.value === null || r.value === undefined || r.value < ticks[0] || r.value > ticks[ticks.length - 1]) continue;
    svg.append(svgEl("line", { class: "refline", x1: m.l, x2: W - m.r, y1: Y(r.value), y2: Y(r.value) }));
    const lt = svgEl("text", { class: "reflabel", x: W - m.r - 4, y: Y(r.value) - 5, "text-anchor": "end" });
    lt.textContent = r.label;
    svg.append(lt);
  }

  const ends = [];
  for (const s of series) {
    let dpath = "", pen = false, last = null;
    data.forEach((d, i) => {
      const v = d[s.key];
      if (v === null || v === undefined) { pen = false; return; }
      dpath += `${pen ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true; last = { x: X(xs[i]), y: Y(v), v };
    });
    svg.append(svgEl("path", { class: `series${s.dashed ? " dashed" : ""}`, d: dpath, stroke: `var(${s.color})` }));
    if (last && endLabels) ends.push({ ...last, s });
  }
  // direct end labels, nudged apart so they never collide
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
  for (const e of ends) {
    const t = svgEl("text", { class: "endlabel", x: W - m.r + 6, y: e.y + 4 });
    t.textContent = e.s.label;
    svg.append(t);
  }

  // hover layer: crosshair snaps to the nearest sample, tooltip lists every series
  const cross = svgEl("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const dots = series.map((s) => svgEl("circle", { class: "dot", r: 4, fill: `var(${s.color})`, visibility: "hidden" }));
  const hit = svgEl("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent", tabindex: 0 });
  svg.append(cross, ...dots, hit);
  const at = (i, evt) => {
    const d = data[i], x = X(xs[i]);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      const v = d[s.key];
      dots[k].setAttribute("visibility", v === null || v === undefined ? "hidden" : "visible");
      if (v !== null && v !== undefined) { dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", Y(v)); }
    });
    showTip(evt, (x1 - x0 > 50 * 3.6e6 ? dateTime : dayTime)(d.t), series.map((s) => ({ color: `var(${s.color})`, label: s.label, value: f(d[s.key]) })));
  };
  const nearest = (clientX) => {
    const r = svg.getBoundingClientRect();
    const t = x0 + ((clientX - r.left) * (W / r.width) - m.l) / (W - m.l - m.r) * (x1 - x0);
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(x - t) < Math.abs(xs[best] - t)) best = i; });
    return best;
  };
  let focusIdx = data.length - 1;
  hit.addEventListener("pointermove", (e) => at(nearest(e.clientX), e));
  hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
  hit.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    focusIdx = Math.max(0, Math.min(data.length - 1, focusIdx + (e.key === "ArrowLeft" ? -1 : 1)));
    const r = hit.getBoundingClientRect();
    at(focusIdx, { clientX: r.left + (X(xs[focusIdx]) - m.l) * (r.width / (W - m.l - m.r)), clientY: r.top + 20 });
  });
  hit.addEventListener("blur", hideTip);
  container.append(svg);
}

/** Hourly forecast bars. bars: [{start: iso, kwh}] */
export function barChart(container, bars, now) {
  container.replaceChildren();
  if (!bars.length) return;
  const W = container.clientWidth || 600, H = container.clientHeight || 140;
  const m = { l: 34, r: 8, t: 6, b: 20 };
  const max = Math.max(0.1, ...bars.map((b) => b.kwh));
  const ticks = niceTicks(0, max, 3);
  const top = ticks[ticks.length - 1];
  const bw = (W - m.l - m.r) / bars.length;
  const Y = (v) => m.t + (1 - v / top) * (H - m.t - m.b);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "PV-Prognose je Stunde" });
  const axis = svgEl("g", { class: "axis" });
  for (const v of ticks) {
    axis.append(svgEl("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const t = svgEl("text", { x: m.l - 5, y: Y(v) + 4, "text-anchor": "end" });
    t.textContent = fmt(v, top < 2 ? 1 : 0);
    axis.append(t);
  }
  svg.append(axis);
  const nowT = now.getTime();
  bars.forEach((b, i) => {
    const start = new Date(b.start), x = m.l + i * bw;
    if (start.getHours() % 6 === 0) {
      const t = svgEl("text", { class: "", x: x + bw / 2, y: H - 4, "text-anchor": "middle", fill: "var(--text-muted)", "font-size": 11 });
      t.textContent = start.getHours() === 0 ? start.toLocaleDateString("de-DE", { weekday: "short" }) : `${start.getHours()}h`;
      svg.append(t);
    }
    if (b.kwh <= 0.005) return;
    const y = Y(b.kwh), h = Math.max(1, H - m.b - y);
    const past = start.getTime() + 3.6e6 <= nowT;
    const r = svgEl("rect", { class: `bar${past ? " past" : ""}`, x: x + 1, y, width: Math.max(1, bw - 2), height: h, rx: Math.min(4, bw / 3), tabindex: 0 });
    const tip = (e) => showTip(e, `${dayTime(b.start)} – ${time(new Date(start.getTime() + 3.6e6).toISOString())}`,
      [{ color: "var(--series-1)", label: "Erwartet", value: fmt(b.kwh, 2, "kWh") }]);
    r.addEventListener("pointermove", tip);
    r.addEventListener("focus", (e) => { const bb = r.getBoundingClientRect(); tip({ clientX: bb.right, clientY: bb.top }); });
    r.addEventListener("pointerleave", hideTip);
    r.addEventListener("blur", hideTip);
    svg.append(r);
  });
  const xNow = m.l + ((nowT - new Date(bars[0].start).getTime()) / 3.6e6) * bw;
  if (xNow > m.l && xNow < W - m.r) svg.append(svgEl("line", { class: "now", x1: xNow, x2: xNow, y1: m.t, y2: H - m.b }));
  container.append(svg);
}

export function legend(container, series) {
  container.replaceChildren(...series.map((s) =>
    el("span", {}, el("i", { class: s.dashed ? "dash" : "", style: `background:var(${s.color});color:var(${s.color})` }), s.label)));
}

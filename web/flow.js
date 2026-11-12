// Energy-flow diagram: lays out the site topology (from /api/state → flow) as a
// top-down tree and animates each connection in the direction power flows.
import { t } from "./i18n.js";
import { el, power } from "./lib.js";
import { icon } from "./icons.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const text = (attrs, content) => { const node = svgEl("text", attrs); node.textContent = content; return node; };

const NODE_W = 176, NODE_H = 100, COL_GAP = 22, ROW_GAP = 46;
// role colours: producers blue, consumers orange (see style.css)
const SOURCE_COLOR = { pv: "--prod-pv", battery: "--prod-bat", grid: "--prod-grid" };
const SINK_COLOR = { house: "--cons-house", heatpump: "--cons-hp", charger: "--cons-car", battery: "--cons-bat", grid: "--cons-grid" };
const NODE_COLOR = {
  source: { pv: "--prod-pv", inverter: "--prod-pv", battery: "--prod-bat", grid: "--prod-grid" },
  sink: { house: "--cons-house", heatpump: "--cons-hp", charger: "--cons-car", battery: "--cons-bat", grid: "--cons-grid" },
};
const nodeColor = (n) => (NODE_COLOR[n.role] || {})[n.kind] || NODE_COLOR.source[n.kind] || NODE_COLOR.sink[n.kind] || "--text-muted";

function layout(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, { ...n, children: [] }]));
  const roots = [];
  for (const n of byId.values()) (n.parent && byId.has(n.parent) ? byId.get(n.parent).children : roots).push(n);
  let col = 0, depthMax = 0;
  const place = (n, depth) => {
    n.depth = depth; depthMax = Math.max(depthMax, depth);
    if (!n.children.length) { n.col = col++; return; }
    n.children.forEach((c) => place(c, depth + 1));
    n.col = (n.children[0].col + n.children[n.children.length - 1].col) / 2;
  };
  roots.forEach((r) => place(r, 0));
  for (const n of byId.values()) {
    n.x = n.col * (NODE_W + COL_GAP);
    n.y = n.depth * (NODE_H + ROW_GAP);
  }
  return { byId, width: col * (NODE_W + COL_GAP) - COL_GAP, height: (depthMax + 1) * (NODE_H + ROW_GAP) - ROW_GAP };
}

export function renderFlow(container, flow) {
  container.replaceChildren();
  if (!flow || !flow.nodes.length) {
    container.append(el("div", { class: "empty" }, t("web.flow.keine_topologie_verfuegbar")));
    return;
  }
  const { byId, width, height } = layout(flow.nodes);
  const pad = 4;
  // shrink to fit narrow screens, but never below a readable size (then scroll)
  const scale = Math.min(1, Math.max(0.62, (container.clientWidth || width) / (width + 2 * pad)));
  const svg = svgEl("svg", {
    viewBox: `${-pad} ${-pad} ${width + 2 * pad} ${height + 2 * pad}`,
    width: ((width + 2 * pad) * scale).toFixed(0), height: ((height + 2 * pad) * scale).toFixed(0), role: "img",
    "aria-label": t("web.flow.energiefluss_zwischen_netz"),
  });

  const maxW = Math.max(500, ...flow.edges.map((e) => e.w || 0));
  for (const e of flow.edges) {
    const a = byId.get(e.from), b = byId.get(e.to);
    if (!a || !b) continue;
    // draw from the upper node to the lower one; the animation direction follows the flow
    const [up, down] = a.depth <= b.depth ? [a, b] : [b, a];
    const x1 = up.x + NODE_W / 2, y1 = up.y + NODE_H, x2 = down.x + NODE_W / 2, y2 = down.y;
    const ym = (y1 + y2) / 2;
    const d = `M${x1},${y1} V${ym} H${x2} V${y2}`;
    const active = e.w !== null && e.w >= 20;
    // colour a connection by its device end (the lower node), the grid line by the grid
    const color = "var(--flow-dot)";                                     // one colour for all moving dots
    const path = svgEl("path", {
      d, class: `flow-edge${e.w === null ? " unmetered" : active ? " active" : " idle"}`,
      stroke: active ? color : "var(--border)",
      "stroke-width": active ? (2 + 4 * Math.sqrt(e.w / maxW)).toFixed(1) : 2,
    });
    if (active) {
      const downward = a === up;
      path.style.animationDuration = `${Math.max(0.6, 3.2 - 2.6 * Math.sqrt(e.w / maxW)).toFixed(2)}s`;
      path.style.animationDirection = downward ? "normal" : "reverse";
    }
    svg.append(path);
    if (e.w !== null) {
      const p = power(e.w);
      const lx = x1 === x2 ? x1 + 8 : (x1 + x2) / 2;
      svg.append(text({ class: "flow-label", x: lx, y: ym - 6, "text-anchor": x1 === x2 ? "start" : "middle" },
        `${p.v} ${p.u}`));
    }
  }

  for (const n of byId.values()) {
    const color = `var(${nodeColor(n)})`;
    const g = svgEl("g", { class: `flow-node kind-${n.kind} role-${n.role || "pass"}`, transform: `translate(${n.x},${n.y})`,
      style: `--node-color:${color}` });
    g.append(svgEl("rect", { width: NODE_W, height: NODE_H, rx: 10, class: "flow-box" }));
    const ic = icon(n.kind, { size: 20, x: 12, y: 10, className: "flow-icon" });
    g.append(ic);
    const label = n.label.length > 19 ? `${n.label.slice(0, 18)}…` : n.label;
    g.append(text({ class: "flow-title", x: 38, y: 25 }, label));
    const value = n.power_w === null ? "–" : (() => { const p = power(Math.abs(n.power_w)); return `${p.v} ${p.u}`; })();
    const unmetered = n.kind === "heatpump" && n.power_w === null;
    g.append(text({ class: `flow-value${unmetered ? " muted" : ""}`, x: 12, y: 54 }, unmetered ? t("web.flow.messung_startet") : value));
    n.details.slice(0, 2).forEach((detail, k) =>
      g.append(text({ class: "flow-detail", x: 12, y: 76 + k * 14 }, detail.length > 31 ? `${detail.slice(0, 30)}…` : detail)));
    const title = svgEl("title");
    title.textContent = [n.label, value, n.role_text, ...n.details].filter(Boolean).join("\n");
    g.append(title);
    svg.append(g);
  }
  container.append(svg);
  container.scrollLeft = (container.scrollWidth - container.clientWidth) / 2;   // keep the grid in view
}

/** Two stacked bars on one scale: what supplies power now, and what uses it. */
export function renderBalance(container, balance) {
  container.replaceChildren();
  if (!balance || !balance.total_w) return;
  const total = balance.total_w;
  const row = (title, mark, items, palette) => {
    const bar = el("div", { class: "bal-bar", role: "img",
      "aria-label": `${title}: ${items.map((i) => `${i.label} ${fmtW(i.w)}`).join(", ")}` });
    for (const it of items) {
      bar.append(el("div", { class: "bal-seg", title: `${it.label}: ${fmtW(it.w)}`,
        style: `flex-basis:${(100 * it.w / total).toFixed(2)}%;background:var(${palette[it.kind] || "--text-muted"})` }));
    }
    const list = el("div", { class: "bal-list" }, items.map((it) => el("span", {},
      el("i", { style: `background:var(${palette[it.kind] || "--text-muted"})` }), it.label, " ", el("b", {}, fmtW(it.w)))));
    return el("div", { class: "bal-row" }, el("div", { class: "bal-title" }, `${mark} ${title}`), bar, list);
  };
  container.append(row(t("web.flow.erzeugung"), "▲", balance.sources, SOURCE_COLOR), row(t("web.flow.verbrauch"), "▼", balance.sinks, SINK_COLOR));
}

function fmtW(w) {
  const p = power(w);
  return `${p.v} ${p.u}`;
}

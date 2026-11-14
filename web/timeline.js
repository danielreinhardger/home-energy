// Overview charts over time: power balance, hot water, battery – with the history they draw from.
// Up to 48 h the page keeps the minute data itself (loaded once, then only new minutes);
// longer ranges come from the database, already averaged to about the chart width.
import { t } from "./i18n.js";
import { api, el, fmt, dayTime, clear } from "./lib.js";
import { lineChart, legend } from "./charts.js";
import { balanceChart, balanceLegend } from "./balancechart.js";

const $ = (id) => document.getElementById(id);
const POWER_SERIES = () => [
  { key: "pv_w", label: "PV", color: "--series-1" },
  { key: "house_w", label: t("web.overview.haus"), color: "--series-2" },
  { key: "battery_w", label: t("web.overview.batterie"), color: "--series-3" },
  { key: "grid_w", label: t("web.overview.netz"), color: "--series-4" },
  { key: "car_w", label: t("web.overview.auto"), color: "--series-5" },
];
const DHW_SERIES = () => [
  { key: "dhw_temp", label: t("web.timeline.ist"), color: "--series-1" },
  { key: "dhw_target", label: t("web.timeline.soll"), color: "--series-2", dashed: true },
];
const LIVE_HOURS = 48;          // up to here the charts use the minute data kept in the page
const HISTORY_MS = LIVE_HOURS * 3.6e6;

const view = { range: 12, history: [], long: null, showTable: false, state: null };

// The server sends history as columns ({t: [unix s…], pv_w: […], …}): each name once, not per row.
// Back into rows here ({t: ms, pv_w, …}) – the charts read rows; new Date(ms) works like an ISO time.
function fromColumns(c) {
  const keys = Object.keys(c).filter((k) => k !== "t");
  return c.t.map((ts, i) => {
    const row = { t: ts * 1000 };
    for (const k of keys) row[k] = c[k][i];
    return row;
  });
}

/** Wire the range buttons and the table toggle (once, at start). */
export function setupTimeline() {
  for (const b of document.querySelectorAll("[data-range]")) {
    b.addEventListener("click", () => {
      view.range = Number(b.dataset.range);
      for (const o of document.querySelectorAll("[data-range]")) o.setAttribute("aria-pressed", o === b);
      renderCharts();
      if (view.range > LIVE_HOURS) refreshLong();
    });
  }
  $("toggle-table").addEventListener("click", () => {
    view.showTable = !view.showTable;
    $("power-table").hidden = !view.showTable;
    $("toggle-table").textContent = view.showTable ? "Diagramm" : t("web.html.tabelle");
    $("power-chart").hidden = view.showTable;
    renderCharts();
  });
  let resizeTimer;
  addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderCharts, 150); });
}

/** The latest state (for the battery's discharge limit line). */
export function setTimelineState(state) {
  view.state = state;
}

// The first load fetches the 48 h once; after that only the new minutes (a few hundred bytes).
export async function refreshHistory() {
  try {
    const last = view.history.at(-1)?.t;
    const loaded = fromColumns(await api(last ? `/api/history?since=${Math.floor(last / 1000)}&format=columns`
      : "/api/history?format=columns"));
    // Two refreshes can overlap (returning to the tab fires one, the minute timer another): both asked
    // from the same last minute, so take only what is newer than what the page holds by now.
    const have = view.history.at(-1)?.t ?? -Infinity;
    const fresh = loaded.filter((d) => d.t > have);
    if (!fresh.length) return;
    const cut = Date.now() - HISTORY_MS;
    view.history = view.history.concat(fresh).filter((d) => d.t >= cut);
    renderCharts();
  } catch {}
}

// Longer ranges come from the database, already averaged to about the chart width
// (minute data or 15-minute averages underneath); refreshed every 5 minutes.
export async function refreshLong() {
  const range = view.range;
  if (range <= LIVE_HOURS) return;
  if (view.long?.range === range && Date.now() - view.long.at < 300000) return;
  const from = new Date(Date.now() - range * 3.6e6).toISOString();
  const points = Math.max(150, Math.round(($("power-chart").clientWidth || 800) / 2));
  try {
    const res = await api(`/api/history?from=${encodeURIComponent(from)}&points=${points}&format=columns`);
    if (view.range !== range) return;                         // the user switched meanwhile
    view.long = { range, at: Date.now(), data: fromColumns(res.samples), resolution: res.resolution_s };
    renderCharts();
  } catch {}
}

// Average neighbouring samples so a chart never draws more points than it has pixels.
function thin(data, maxPoints) {
  if (data.length <= maxPoints) return data;
  const size = Math.ceil(data.length / maxPoints), out = [];
  for (let i = 0; i < data.length; i += size) {
    const bucket = data.slice(i, i + size), row = { t: bucket[0].t };
    for (const key of Object.keys(bucket[0])) {
      if (key === "t") continue;
      const nums = bucket.map((d) => d[key]).filter((v) => typeof v === "number");
      row[key] = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : bucket.at(-1)[key];
    }
    out.push(row);
  }
  return out;
}

export function renderCharts() {
  if ($("tab-overview").hidden) return;
  const since = Date.now() - view.range * 3.6e6;
  const all = view.range > LIVE_HOURS
    ? (view.long?.range === view.range ? view.long.data : [])
    : view.history.filter((d) => d.t >= since);
  const data = thin(all, Math.max(150, Math.round(($("power-chart").clientWidth || 800) / 2)));
  balanceLegend($("power-legend"));
  legend($("dhw-legend"), DHW_SERIES());
  if (view.showTable) renderPowerTable(all);
  else balanceChart($("power-chart"), data);
  lineChart($("dhw-chart"), data, DHW_SERIES(), { unit: "°C", digits: 1 });
  const ko = Object.fromEntries((view.state?.devices.kostal?.values || []).map((v) => [v.key, v.value]));
  const minSoc = ko.battery_min_soc;
  lineChart($("soc-chart"), data, [{ key: "soc", label: "SOC", color: "--prod-bat" }],
    { unit: "%", yMin: 0, yMax: 100, endLabels: false,
      refLines: minSoc === null || minSoc === undefined ? [] : [{ value: minSoc, label: `Entladegrenze ${fmt(minSoc, 0)} %` }] });
}

function renderPowerTable(data) {
  const rows = data.slice(-120).reverse();
  clear($("power-table"), el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, t("web.controltab.zeit")), POWER_SERIES().map((s) => el("th", { class: "num" }, `${s.label} (W)`)),
      el("th", { class: "num" }, "SOC"), el("th", { class: "num" }, "WW °C"))),
    el("tbody", {}, rows.map((d) => el("tr", {}, el("td", {}, dayTime(d.t)),
      POWER_SERIES().map((s) => el("td", { class: "num" }, fmt(d[s.key], 0))),
      el("td", { class: "num" }, fmt(d.soc, 0)), el("td", { class: "num" }, fmt(d.dhw_temp, 1)))))));
}

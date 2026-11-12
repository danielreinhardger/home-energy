// Finanzen tab: today's money and the merit order (from the live state), and the daily/monthly
// ledger from /api/finance. Ledger bars: feed-in income above zero, grid cost below, a dot for
// the day's balance, one € axis.
import { locale, t } from "./i18n.js";
import { api, el, fmt, clear } from "./lib.js";
import { showTip, hideTip, niceTicks } from "./charts.js";
import { meritBars, copLine, simulationCard } from "./explain.js";

const $ = (id) => document.getElementById(id);

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const eur = (v) => (v === null || v === undefined ? "–" : `${fmt(v, 2)} €`);
// Heat pump electricity of a day/month, plus the backup heater (Heizstab) where it ran: its kWh are
// electricity 1:1 and cost about three times a heat pump kWh, so they are shown, not hidden in the sum.
const hpPower = (r) => {
  const hp = `${fmt(r.hp_power_heating_kwh + r.hp_power_dhw_kwh, 1)} kWh`;
  return (r.backup_kwh ?? 0) >= 0.5
    ? el("span", {}, hp, el("br"), el("small", { class: "warn" }, t("web.finance.kwh_heizstab", { r: fmt(r.backup_kwh, 0) }))) : hp;
};
const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), { weekday: "short", day: "2-digit", month: "2-digit" });
const monthLabel = (ym) => new Date(`${ym}-15T12:00:00`).toLocaleDateString(locale(), { month: "long", year: "numeric" });

/** Today's grid cost, feed-in income and balance (also shown on the overview). */
export function moneyBoxes(eco) {
  const today = eco.today;
  return el("div", { class: "money" },
    el("div", {}, el("span", {}, t("web.explain.netzbezug")), el("b", {}, eur(today.cost_eur)), el("small", {}, `${fmt(today.import_kwh, 1)} kWh × ${fmt(eco.prices.buy * 100, 1)} ct`)),
    el("div", {}, el("span", {}, t("web.overview.einspeisung")), el("b", {}, eur(today.earn_eur)), el("small", {}, `${fmt(today.export_kwh, 1)} kWh × ${fmt(eco.prices.feed * 100, 1)} ct`)),
    el("div", { class: "net" }, el("span", {}, t("web.finance.saldo_heute")), el("b", {}, eur(today.net_eur)), el("small", {}, t("web.finance.einspeisung_bezug"))));
}

/** The parts that follow the live state (while the tab is shown): today's money and the merit order. */
export function renderFinanceLive(eco) {
  if (!eco) return;
  clear($("money-fin"), moneyBoxes(eco));
  clear($("merit"), meritBars(eco), copLine(eco));
}

/** The ledger (when the tab opens and every 5 minutes while it is shown). */
export async function refreshFinance() {
  try {
    renderFinance($("finance"), await api("/api/finance"));
    if (!$("simulation").children.length) $("simulation").append(simulationCard());
  } catch (err) {
    clear($("finance"), el("p", { class: "msg err" }, err.message));
  }
}

function dailyChart(container, days) {
  container.replaceChildren();
  const rows = [...days].reverse().slice(-31);                    // oldest → newest, last 31 days
  if (!rows.length) {
    container.append(el("div", { class: "empty" }, t("web.finance.noch_keine_tage_erfasst_der_erste")));
    return;
  }
  const W = container.clientWidth || 600, H = 240, m = { l: 48, r: 10, t: 12, b: 26 };
  const hi = Math.max(0.5, ...rows.map((r) => r.earn_eur)), lo = -Math.max(0.5, ...rows.map((r) => r.cost_eur));
  const ticks = niceTicks(lo, hi, 4), y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const bw = (W - m.l - m.r) / Math.max(rows.length, 7);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("web.finance.einspeiseerloes_und_netzkosten_pro_tag") });
  for (const v of ticks) {
    svg.append(svgEl("line", { class: v === 0 ? "zero" : "gridline", x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    const label = svgEl("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", class: "x-axis" });
    label.textContent = `${fmt(v, 0)} €`;
    svg.append(label);
  }
  rows.forEach((r, i) => {
    const x = m.l + i * bw + 2, w = Math.max(2, bw - 4);
    const up = svgEl("rect", { x, y: Y(r.earn_eur), width: w, height: Math.max(0, Y(0) - Y(r.earn_eur)), rx: Math.min(4, w / 3), class: "fin-earn" });
    const down = svgEl("rect", { x, y: Y(0), width: w, height: Math.max(0, Y(-r.cost_eur) - Y(0)), rx: Math.min(4, w / 3), class: "fin-cost" });
    const dot = svgEl("circle", { cx: x + w / 2, cy: Y(r.net_eur), r: 3.5, class: "fin-net" });
    const hit = svgEl("rect", { x: m.l + i * bw, y: m.t, width: bw, height: H - m.t - m.b, fill: "transparent", tabindex: 0 });
    const tip = (e) => showTip(e, dayLabel(r.day), [
      { color: "var(--series-4)", label: t("web.finance.einspeisung_kwh", { r: fmt(r.export_kwh, 1) }), value: eur(r.earn_eur) },
      { color: "var(--series-4)", label: t("web.finance.netzbezug_kwh", { r: fmt(r.import_kwh, 1) }), value: eur(-r.cost_eur) },
      { color: "var(--text-primary)", label: t("web.finance.saldo"), value: eur(r.net_eur) },
      { color: "var(--series-1)", label: t("web.finance.vorteil_durch_pv"), value: eur(r.benefit_eur) },
    ]);
    hit.addEventListener("pointermove", tip);
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("focus", () => { const b = hit.getBoundingClientRect(); tip({ clientX: b.right, clientY: b.top + 20 }); });
    hit.addEventListener("blur", hideTip);
    svg.append(up, down, dot, hit);
    if (rows.length <= 10 || i % Math.ceil(rows.length / 10) === 0) {
      const label = svgEl("text", { x: x + w / 2, y: H - 8, "text-anchor": "middle", class: "x-axis" });
      label.textContent = new Date(`${r.day}T12:00:00`).toLocaleDateString(locale(), { day: "2-digit", month: "2-digit" });
      svg.append(label);
    }
  });
  container.append(svg);
}

function table(head, rows) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, head.map((h, i) => el("th", { class: i ? "num" : "" }, h)))),
    el("tbody", {}, rows)));
}

function renderFinance(root, data) {
  const days = data.days || [], months = data.months || [];
  const chart = el("div", { class: "fin-chart" });
  clear(root,
    el("div", { class: "legend" },
      el("span", {}, el("i", { class: "box", style: "background:var(--series-4)" }), t("web.finance.einspeisung_ertrag")),
      el("span", {}, el("i", { class: "box", style: "background:var(--series-4);opacity:.55" }), t("web.finance.netzbezug_kosten")),
      el("span", {}, el("i", { class: "dot" }), t("web.finance.saldo_des_tages"))),
    chart,
    el("h3", {}, t("web.finance.pro_monat")),
    months.length ? table([t("web.finance.monat"), t("web.finance.tage"), t("web.explain.netzbezug"), t("web.finance.kosten"), t("web.overview.einspeisung"), t("web.finance.ertrag"), t("web.finance.saldo"), "PV", t("web.finance.wp_strom"), t("web.finance.cop_heizen_ww"), t("web.finance.vorteil_durch_pv")],
      months.map((m) => el("tr", {},
        el("td", {}, monthLabel(m.month)), el("td", { class: "num" }, m.days),
        el("td", { class: "num" }, `${fmt(m.import_kwh, 1)} kWh`), el("td", { class: "num" }, eur(m.cost_eur)),
        el("td", { class: "num" }, `${fmt(m.export_kwh, 1)} kWh`), el("td", { class: "num" }, eur(m.earn_eur)),
        el("td", { class: "num" }, el("b", {}, eur(m.net_eur))), el("td", { class: "num" }, `${fmt(m.pv_kwh, 1)} kWh`),
        el("td", { class: "num" }, hpPower(m)),
        el("td", { class: "num" }, `${fmt(m.cop_heating, 1)} / ${fmt(m.cop_dhw, 1)}`),
        el("td", { class: "num" }, eur(m.benefit_eur))))) : el("p", { class: "msg" }, t("web.finance.noch_keine_monate_erfasst")),
    el("h3", {}, t("web.finance.pro_tag")),
    days.length ? table([t("web.finance.tag"), t("web.explain.netzbezug"), t("web.finance.kosten"), t("web.overview.einspeisung"), t("web.finance.ertrag"), t("web.finance.saldo"), "PV", t("web.overview.haus"), t("web.overview.auto"), t("web.finance.wp_strom"), t("web.finance.cop_heizen_ww"), t("web.finance.vorteil_durch_pv")],
      days.map((r) => el("tr", {},
        el("td", {}, dayLabel(r.day)),
        el("td", { class: "num" }, `${fmt(r.import_kwh, 1)} kWh`), el("td", { class: "num" }, eur(r.cost_eur)),
        el("td", { class: "num" }, `${fmt(r.export_kwh, 1)} kWh`), el("td", { class: "num" }, eur(r.earn_eur)),
        el("td", { class: "num" }, el("b", {}, eur(r.net_eur))), el("td", { class: "num" }, `${fmt(r.pv_kwh, 1)} kWh`),
        el("td", { class: "num" }, `${fmt(r.house_kwh, 1)} kWh`), el("td", { class: "num" }, `${fmt(r.car_kwh, 1)} kWh`),
        el("td", { class: "num" }, hpPower(r)),
        el("td", { class: "num" }, `${fmt(r.cop_heating, 1)} / ${fmt(r.cop_dhw, 1)}`),
        el("td", { class: "num" }, eur(r.benefit_eur))))) : el("p", { class: "msg" }, t("web.finance.noch_keine_tage_erfasst")),
    el("p", { class: "flow-source" }, t("web.finance.vorteil_durch_pv_was_haus_und") +
      t("web.finance.wp_strom_und_cop_aus_den") +
      t("web.finance.erfassung_seit_inbetriebnahme_dieser")));
  requestAnimationFrame(() => dailyChart(chart, days));
}

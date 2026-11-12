// Dashboard wiring: tabs, polling and handing each new state to the visible tab only
// (a hidden tab is drawn when it is opened).
// Each tab draws itself in its own module: overview.js (with timeline.js for the charts),
// controltab.js, explain.js, finance.js, details.js, logtab.js; the settings forms come from forms.js.
import { time } from "./lib.js";
import { loadState } from "./state.js";
import { setupTimeline, setTimelineState, refreshHistory, refreshLong, renderCharts } from "./timeline.js";
import { renderOverview } from "./overview.js";
import { renderLogs, loadSettings } from "./controltab.js";
import { renderExplain } from "./explain.js";
import { renderFinanceLive, refreshFinance } from "./finance.js";
import { renderDetails } from "./details.js";
import { renderAlerts } from "./alerts.js";
import { setupLog, refreshLog } from "./logtab.js";
import { setupOutlook } from "./outlook.js";

const $ = (id) => document.getElementById(id);
let state = null;
let current = "overview";

// ---- tabs -------------------------------------------------------------------
function selectTab(name) {
  current = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", b.dataset.tab === name);
  for (const s of document.querySelectorAll(".tab")) s.hidden = s.id !== `tab-${name}`;
  try { localStorage.setItem("tab", name); } catch {}
  if (name === "settings" || name === "control") loadSettings(refreshState);
  if (name === "overview") renderCharts();
  if (name === "log") refreshLog();
  if (name === "finance") refreshFinance();
  if (state) renderTab(name, state);
}

// ---- state ------------------------------------------------------------------
async function refreshState() {
  try {
    state = await loadState();
    render(state);
    const errs = Object.values(state.devices)
      .filter((d) => d.error || d.stale)
      .map((d) => `${d.name}: ${d.error || "keine aktuellen Daten – Regelung pausiert"}`);
    $("status").textContent = errs.length ? errs.join(" · ") : `aktualisiert ${time(state.now)}`;
    $("status").className = `status${errs.length ? " err" : ""}`;
  } catch (err) {
    $("status").textContent = `Server nicht erreichbar: ${err.message}`;
    $("status").className = "status err";
  }
}

function render(s) {
  renderAlerts(s.alerts);
  setTimelineState(s);
  renderTab(current, s);
}

// What each tab draws from the state (the others fetch their own data: finance ledger, log, settings).
function renderTab(name, s) {
  if (name === "overview") renderOverview(s, refreshState);
  else if (name === "details") renderDetails(s.devices);
  else if (name === "control") renderLogs(s);
  else if (name === "finance") renderFinanceLive(s.economy);
  else if (name === "explain") renderExplain($("explain"), s);
}

// ---- start ------------------------------------------------------------------
for (const btn of document.querySelectorAll(".tabs button")) btn.addEventListener("click", () => selectTab(btn.dataset.tab));
setupTimeline();
setupLog();
setupOutlook();
let saved = "overview";
try { saved = localStorage.getItem("tab") || saved; } catch {}
selectTab(saved);
refreshState();
refreshHistory();
// Polling pauses while the tab is hidden (background tab, locked phone) and catches up on return.
const visible = () => document.visibilityState === "visible";
setInterval(() => { if (visible()) refreshState(); }, 10000);
setInterval(() => { if (visible()) { refreshHistory(); refreshLong(); } }, 60000);
setInterval(() => { if (visible() && !$("tab-finance").hidden) refreshFinance(); }, 300000);
setInterval(() => { if (visible() && !$("tab-log").hidden) refreshLog(); }, 30000);
document.addEventListener("visibilitychange", () => {
  if (!visible()) return;
  refreshState();
  refreshHistory();
});

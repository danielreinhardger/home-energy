// Dashboard wiring: tabs, polling and handing each new state to the visible tab only
// (a hidden tab is drawn when it is opened).
// Each tab draws itself in its own module: overview.js (with timeline.js for the charts),
// controltab.js, explain.js, finance.js, details.js, logtab.js; the settings forms come from forms.js.
import { time } from "./lib.js";
import { applyStatic, t } from "./i18n.js";
import { loadMeta, loadState } from "./state.js";
import { setupTimeline, setTimelineState, refreshHistory, refreshLong, renderCharts } from "./timeline.js";
import { renderOverview } from "./overview.js";
import { renderLogs, loadSettings } from "./controltab.js";
import { renderExplain } from "./explain.js";
import { renderFinanceLive, refreshFinance } from "./finance.js";
import { renderDetails } from "./details.js";
import { renderAlerts } from "./alerts.js";
import { setupLog, refreshLog } from "./logtab.js";
import { setupOutlook } from "./outlook.js";
import { updateBackground } from "./background.js";

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
      .map((d) => `${d.name}: ${d.error || t("web.app.keine_aktuellen_daten_regelung_pausiert")}`);
    $("status").textContent = errs.length ? errs.join(" · ") : `aktualisiert ${time(state.now)}`;
    $("status").className = `status${errs.length ? " err" : ""}`;
  } catch (err) {
    $("status").textContent = t("web.app.server_nicht_erreichbar", { message: err.message });
    $("status").className = "status err";
  }
}

function render(s) {
  renderAlerts(s.alerts);
  setTimelineState(s);
  renderTab(current, s);
  try { updateBackground(s); } catch (err) { console.warn(err); }   // decoration: never in the way of the page
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
applyStatic();                                    // cached texts until the server's arrive
try { await loadMeta(); } catch {}                // the texts first: everything below draws with them
for (const btn of document.querySelectorAll(".tabs button")) btn.addEventListener("click", () => selectTab(btn.dataset.tab));
setupTimeline();
setupLog();
setupOutlook();
// Phones: the header (title, wrapped tabs, status) takes a third of the screen, so it slides away
// while scrolling down and comes back on scrolling up (style.css: .top.away, narrow screens only).
let lastY = 0;
window.addEventListener("scroll", () => {
  const y = window.scrollY;
  if (Math.abs(y - lastY) < 8) return;                // ignore jitter
  document.querySelector(".top").classList.toggle("away", y > lastY && y > 80);
  lastY = y;
}, { passive: true });
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

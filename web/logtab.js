// Protokoll tab: the error log from the database (/api/log), newest first.
import { t } from "./i18n.js";
import { api, el, clear, dayTime, dateTime } from "./lib.js";

const LEVEL = { error: "err", warning: "warn", info: "info" };       // texts: web.log.level.<level>
let filter = "";

export function setupLog() {
  for (const b of document.querySelectorAll("[data-log-filter]")) {
    b.addEventListener("click", () => {
      filter = b.dataset.logFilter;
      for (const o of document.querySelectorAll("[data-log-filter]")) o.setAttribute("aria-pressed", o === b);
      refreshLog();
    });
  }
}

export async function refreshLog() {
  const box = document.getElementById("event-log");
  try {
    const rows = await api(`/api/log?limit=500${filter ? `&level=${filter}` : ""}`);
    clear(box, rows.length ? el("table", { class: "log" },
      el("thead", {}, el("tr", {}, [t("web.controltab.zeit"), "Art", t("web.logtab.quelle"), t("web.logtab.meldung"), "Anzahl"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((r) => {
        const [label, cls] = LEVEL[r.level] ? [t(`web.log.level.${r.level}`), LEVEL[r.level]] : [r.level, ""];
        const when = r.count > 1 ? `${dateTime(r.first)} – ${dayTime(r.last)}` : dateTime(r.first);
        return el("tr", { class: `lvl-${cls}` },
          el("td", { class: "nowrap" }, when), el("td", {}, el("span", { class: `lvl ${cls}` }, label)),
          el("td", {}, r.source), el("td", {}, r.message), el("td", { class: "num" }, r.count > 1 ? `${r.count}×` : ""));
      }))) : el("p", { class: "msg" }, t("web.logtab.keine_eintraege")));
  } catch (err) {
    clear(box, el("p", { class: "msg err" }, err.message));
  }
}

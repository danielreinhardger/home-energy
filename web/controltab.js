// Regelung tab: the decision logs of both strategies and the settings forms
// (the forms on the Einstellungen tab load the same way).
import { t } from "./i18n.js";
import { api, el, fmt, dayTime, clear, deNum } from "./lib.js";
import { renderDeviceSettings, renderConfigForm } from "./forms.js";

const $ = (id) => document.getElementById(id);

/** Both decision logs (fetched only while this tab is shown); mode names from the rules' meta. */
export async function renderLogs(s) {
  try {
    const d = await api("/api/decisions");
    renderLog($("heating-log"), d.heating, s.heating?.explain?.modes || {});
    renderLog($("decision-log"), d.dhw, s.controller.explain?.modes || {});
  } catch (err) {
    clear($("decision-log"), el("p", { class: "msg err" }, err.message));
  }
}

function renderLog(node, recent, labels) {
  clear(node, recent.length ? el("table", {},
    el("thead", {}, el("tr", {}, [t("web.controltab.zeit"), t("web.controltab.modus"), t("web.controltab.ziel"), t("web.controltab.geraet"), t("web.overview.aktion"), t("web.controltab.begruendung")].map((h) => el("th", {}, h)))),
    el("tbody", {}, recent.map((d) => el("tr", {},
      el("td", {}, dayTime(d.time)), el("td", {}, labels[d.mode] || d.mode),
      el("td", { class: "num" }, fmt(d.target, 1, "°C")), el("td", { class: "num" }, fmt(d.device_target, 1, "°C")),
      el("td", {}, d.action), el("td", {}, deNum(d.reasons.join(" · "))))))) : el("p", { class: "msg" }, t("web.controltab.noch_keine_entscheidungen")));
}

/** Device settings and the config forms; `onSaved` runs after a form was saved (reloads the state). */
export async function loadSettings(onSaved) {
  const again = () => loadSettings(onSaved);
  try {
    const view = await api("/api/settings");
    renderDeviceSettings($("device-settings"), view, () => setTimeout(again, 500));
    for (const form of document.querySelectorAll("form.cfg")) {
      const section = form.dataset.section;
      if (form.contains(document.activeElement)) continue;   // don't clobber edits in progress
      renderConfigForm(form, section, view.config[section], () => { again(); onSaved(); });
    }
  } catch (err) {
    clear($("device-settings"), el("p", { class: "msg err" }, err.message));
  }
}

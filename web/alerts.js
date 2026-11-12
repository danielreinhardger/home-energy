// Alert banner above every tab: what is wrong right now (alerts.py decides, after a delay).
import { t } from "./i18n.js";
import { el, clear, dayTime } from "./lib.js";

export function renderAlerts(a) {
  const box = document.getElementById("alerts");
  const active = a?.active || [];
  box.hidden = !active.length;
  if (!active.length) return clear(box);
  clear(box,
    el("strong", {}, active.length === 1 ? t("web.alerts.hinweis") : t("web.alerts.hinweise", { active: active.length })),
    el("ul", {}, active.map((x) => el("li", {}, x.text, el("span", { class: "msg" }, t("web.alerts.seit", { x: dayTime(x.since) }))))),
    a.push_error ? el("p", { class: "msg err" }, t("web.alerts.push_fehlgeschlagen", { push_error: a.push_error }))
      : a.push ? null : el("p", { class: "msg" }, t("web.alerts.push_aufs_handy_unter_pv_regelung")));
}

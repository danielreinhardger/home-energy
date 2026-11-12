// Alert banner above every tab: what is wrong right now (alerts.py decides, after a delay).
import { el, clear, dayTime } from "./lib.js";

export function renderAlerts(a) {
  const box = document.getElementById("alerts");
  const active = a?.active || [];
  box.hidden = !active.length;
  if (!active.length) return clear(box);
  clear(box,
    el("strong", {}, active.length === 1 ? "Hinweis" : `${active.length} Hinweise`),
    el("ul", {}, active.map((x) => el("li", {}, x.text, el("span", { class: "msg" }, ` · seit ${dayTime(x.since)}`)))),
    a.push_error ? el("p", { class: "msg err" }, `Push fehlgeschlagen: ${a.push_error}`)
      : a.push ? null : el("p", { class: "msg" }, "Push aufs Handy: unter PV-Regelung → „Hinweise & Push“ eine Adresse eintragen."));
}

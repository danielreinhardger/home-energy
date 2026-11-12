// Details tab: every value each device reports, grouped as the device groups them.
import { el, fmt, clear } from "./lib.js";

// a status code with a known meaning shows as text: "Einspeisen (6)"
const shown = (v) => v.options && v.value != null && v.options[v.value] != null ? `${v.options[v.value]} (${v.value})`
  : typeof v.value === "number" ? fmt(v.value, v.digits, v.unit) : (v.value ?? "–");

export function renderDetails(devices) {
  clear(document.getElementById("device-values"), Object.values(devices).map((dev) => {
    const groups = new Map();
    for (const v of dev.values) groups.set(v.group, [...(groups.get(v.group) || []), v]);
    return el("article", { class: "card" }, el("h2", {}, `${dev.name} · ${dev.host}`),
      dev.error ? el("p", { class: "msg err" }, dev.error) : null,
      [...groups].map(([g, vals]) => [el("h3", {}, g), el("table", {}, el("tbody", {},
        vals.map((v) => el("tr", {}, el("td", {}, v.label),
          el("td", { class: "num" }, shown(v))))))]));
  }));
}

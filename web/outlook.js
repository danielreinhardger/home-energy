// The 3-day weather and PV outlook: opened from the forecast card, fetched only then
// (/api/forecast/outlook – not part of the 10-second state). A native <dialog>: Esc,
// the close button or a click on the backdrop closes it.
import { api, clear, el, fmt } from "./lib.js";
import { icon, weatherIcon, weatherKind } from "./icons.js";

const $ = (id) => document.getElementById(id);
const dayDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });

function pvBars(hours) {
  // a small hourly PV profile: one bar per hour, height relative to the day's best hour
  const max = Math.max(0.05, ...hours.map((h) => h.kwh));
  return el("div", { class: "o-pv-bars", "aria-hidden": "true" },
    hours.map((h) => el("i", { style: `height:${Math.max(2, Math.round(100 * h.kwh / max))}%`, title: `${h.hour} Uhr: ${fmt(h.kwh, 2)} kWh` })));
}

function dayCard(d) {
  const [, text] = weatherKind(d.code);
  return el("section", { class: "o-day", "aria-label": `${d.label}, ${d.weekday} ${dayDate(d.date)}` },
    el("header", {},
      el("div", {}, el("div", { class: "o-label" }, d.label), el("div", { class: "o-date" }, `${d.weekday}, ${dayDate(d.date)}`)),
      weatherIcon(d.code, { size: 44 })),
    el("div", { class: "o-text" }, text),
    el("div", { class: "o-temp" },
      d.tmax != null ? `${fmt(d.tmax, 0)}°` : "–", el("span", {}, d.tmin != null ? ` / ${fmt(d.tmin, 0)}°` : "")),
    el("dl", { class: "o-facts" },
      el("dt", {}, "PV-Prognose"), el("dd", {}, d.pv_kwh != null ? `${fmt(d.pv_kwh, 1)} kWh` : "noch offen"),
      el("dt", {}, "Sonne"), el("dd", {}, d.sunshine_h != null ? `${fmt(d.sunshine_h, 1)} h` : "–"),
      el("dt", {}, "Regen"), el("dd", {}, d.precip_mm != null
        ? `${fmt(d.precip_mm, 1)} mm${d.precip_prob != null ? ` · ${fmt(d.precip_prob, 0)} %` : ""}` : "–"),
      el("dt", {}, "Wind bis"), el("dd", {}, d.wind_max != null ? `${fmt(d.wind_max, 0)} km/h` : "–")),
    d.pv_hours?.length ? pvBars(d.pv_hours) : null,
    el("ol", { class: "o-parts" }, d.parts.map((p) => {
      const [, desc] = weatherKind(p.code, p.night);
      return el("li", { title: `${p.hour} Uhr: ${desc}` },
        el("span", { class: "o-hour" }, `${p.hour} Uhr`),
        weatherIcon(p.code, { size: 24, night: p.night }),
        el("span", { class: "o-ptemp" }, p.temp != null ? `${fmt(p.temp, 0)}°` : "–"));
    })));
}

export async function openOutlook() {
  const dlg = $("outlook");
  const body = $("outlook-body");
  clear(body, el("p", { class: "msg" }, "Lädt …"));
  if (!dlg.open) dlg.showModal();
  try {
    const o = await api("/api/forecast/outlook");
    if (!o.days.length) return clear(body, el("p", { class: "msg err" }, o.error || "Keine Prognose"));
    clear(body,
      el("div", { class: "o-days" }, o.days.map(dayCard)),
      el("p", { class: "flow-source" },
        `Open-Meteo${o.fetched_at ? `, Stand ${new Date(o.fetched_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}` : ""}` +
        ` · PV kalibriert × ${fmt(o.calibration, 2)}` + (o.weather ? "" : " · Wetterdaten fehlen gerade")));
  } catch (err) {
    clear(body, el("p", { class: "msg err" }, err.message));
  }
}

export function setupOutlook() {
  const dlg = $("outlook");
  $("outlook-close").addEventListener("click", () => dlg.close());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // backdrop
  $("outlook-close").replaceChildren(icon("close", { size: 20 }));
}

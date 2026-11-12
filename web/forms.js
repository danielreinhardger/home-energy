// Forms generated from server-side metadata: device settings and dashboard config.
import { api, el, fmt, clear } from "./lib.js";

const flash = (node, text, kind = "") => { node.textContent = text; node.className = `msg ${kind}`; };

function numberInput(value, { min, max, step }, onEnter) {
  return el("input", {
    type: "number", value: value ?? "", min, max, step: step ?? "any",
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); onEnter?.(); } },
  });
}

/** Device settings, grouped like the device's own pages. */
export function renderDeviceSettings(root, view, onChanged) {
  clear(root);
  for (const [devKey, dev] of Object.entries(view.devices)) {
    const card = el("article", { class: "card" }, el("h2", {}, dev.name));
    if (!dev.settings.length) card.append(el("p", { class: "msg" }, "Keine Einstellungen über Modbus verfügbar."));
    const groups = Map.groupBy ? Map.groupBy(dev.settings, (s) => s.group)
      : dev.settings.reduce((m, s) => m.set(s.group, [...(m.get(s.group) || []), s]), new Map());
    for (const [group, items] of groups) {
      card.append(el("h3", {}, group));
      for (const s of items) card.append(settingRow(devKey, s, onChanged));
    }
    if (dev.page_only.length) {
      card.append(el("h3", {}, "Nur auf der Geräteseite einstellbar"),
        el("ul", { class: "page-only" }, dev.page_only.map((p) => el("li", {}, p))));
    }
    root.append(card);
  }
}

function settingRow(devKey, s, onChanged) {
  const locked = !s.writable || s.controlled;
  const msg = el("span", { class: "msg" });
  let input;
  if (Object.keys(s.options).length) {
    input = el("select", { disabled: locked },
      Object.entries(s.options).map(([v, label]) => el("option", { value: v, selected: Number(v) === s.value }, label)));
  } else {
    input = numberInput(s.value, s, () => save());
    input.disabled = locked;
  }
  const save = async () => {
    const value = Number(input.value);
    if (value === s.value) return flash(msg, "unverändert");
    btn.disabled = true;
    flash(msg, "schreibe …");
    try {
      const res = await api(`/api/settings/${devKey}/${s.key}`, { value });
      s.value = res.value;
      flash(msg, `gespeichert: ${fmt(res.value, s.step < 1 ? 1 : 0, s.unit)}`, "ok");
      onChanged?.();
    } catch (err) {
      flash(msg, err.message, "err");
    } finally {
      btn.disabled = locked;
    }
  };
  const btn = el("button", { class: "btn", type: "button", disabled: locked, onclick: save }, "Speichern");
  const help = s.controlled ? "Wird von der PV-Warmwasserregelung gesteuert – Komforttemperatur dort einstellen."
    : !s.writable ? (s.note || "Nur Anzeige.")
    : s.note || (s.min !== s.max ? `Gültig: ${fmt(s.min, s.step < 1 ? 1 : 0)} – ${fmt(s.max, s.step < 1 ? 1 : 0)} ${s.unit}` : "");
  return el("div", { class: `setting${locked ? " locked" : ""}` },
    el("label", {}, s.label),
    el("span", { class: "ctl" }, el("span", { class: "input-unit" }, input, el("span", {}, s.unit)), btn),
    el("div", { class: "help" }, help, " ", msg));
}

/** Dashboard config sections (dhw / forecast / limits) from UI_FIELDS metadata. */
export function renderConfigForm(form, section, conf, onSaved) {
  const title = form.querySelector("h2").cloneNode(true);
  clear(form, title);
  const inputs = {};
  for (const [name, meta] of Object.entries(conf.fields)) {
    const value = conf.values[name];
    let input;
    if (meta.type === "bool") input = el("input", { type: "checkbox", class: "switch", checked: !!value });
    else if (meta.type === "time") input = el("input", { type: "time", value: value ?? "", class: "time" });
    else if (meta.type === "text") input = el("input", { type: "url", value: value ?? "", class: "text", placeholder: "https://ntfy.sh/…" });
    else input = numberInput(value, meta);
    input.id = `${section}-${name}`;
    inputs[name] = input;
    form.append(el("div", { class: "field" },
      el("label", { for: input.id }, meta.label),
      ["bool", "time", "text"].includes(meta.type) ? input : el("span", { class: "input-unit" }, input, el("span", {}, meta.unit)),
      meta.help ? el("div", { class: "help" }, meta.help) : null));
  }
  let arrays = null;
  if (section === "forecast") arrays = arrayEditor(form, conf.values.arrays);
  const msg = el("span", { class: "msg" });
  // alerts: send a test message to the saved push address
  const test = section === "alerts" ? el("button", { class: "btn", type: "button", onclick: async () => {
    flash(msg, "sende …");
    try { await api("/api/alerts/test", {}); flash(msg, "Testnachricht gesendet", "ok"); }
    catch (err) { flash(msg, err.message, "err"); }
  } }, "Testnachricht senden") : null;
  form.append(el("div", { class: "form-actions" }, el("button", { class: "btn primary", type: "submit" }, "Speichern"), test, msg));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const patch = {};
    for (const [name, input] of Object.entries(inputs)) {
      if (input.type === "checkbox") patch[name] = input.checked;
      else if (input.type === "time" || input.type === "url") patch[name] = input.value.trim();
      else patch[name] = input.value === "" ? null : Number(input.value);
    }
    if (arrays) patch.arrays = arrays();
    flash(msg, "speichere …");
    try {
      await api(`/api/config/${section}`, patch);
      flash(msg, "gespeichert", "ok");
      onSaved?.();
    } catch (err) {
      flash(msg, err.message, "err");
    }
  };
}

function arrayEditor(form, arrays) {
  const body = el("tbody");
  const row = (a = { kwp: 5, tilt: 30, azimuth: 0 }) => {
    const tr = el("tr", {},
      ["kwp", "tilt", "azimuth"].map((k) => el("td", {}, el("input", { type: "number", step: "any", value: a[k], "data-k": k }))),
      el("td", {}, el("button", { class: "link", type: "button", onclick: () => tr.remove() }, "entfernen")));
    body.append(tr);
  };
  arrays.forEach(row);
  form.append(el("h3", {}, "Modulflächen"),
    el("div", { class: "help msg" }, "Azimut: 0 = Süd, −90 = Ost, 90 = West. Die Kalibrierung gleicht kWp-Fehler mit dem echten Tagesertrag aus."),
    el("div", { class: "table-wrap" }, el("table", { class: "arrays" },
      el("thead", {}, el("tr", {}, el("th", {}, "kWp"), el("th", {}, "Neigung °"), el("th", {}, "Azimut °"), el("th"))), body)),
    el("button", { class: "link", type: "button", onclick: () => row() }, "+ Fläche"));
  return () => [...body.rows].map((tr) => Object.fromEntries(
    [...tr.querySelectorAll("input")].map((i) => [i.dataset.k, Number(i.value)])));
}

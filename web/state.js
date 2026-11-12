// The state the tabs draw. /api/state (every 10 s) carries only what changes: device values
// as {key: value}, the last decisions, energy, forecast. Labels and rule texts come from
// /api/meta, which is fetched again only when its version (sent in every state) changes –
// after a settings change, say. Here the two are joined back into the shape the tabs use:
// devices[k].values = [{key, label, unit, group, digits, value}], controller/heating.explain.
import { setTexts } from "./i18n.js";
import { api } from "./lib.js";

let meta = null;

/** Labels, rule texts and the page texts in the installation's language (again only when they change). */
export async function loadMeta() {
  meta = await api("/api/meta");
  setTexts(meta);
  return meta;
}

export async function loadState() {
  const s = await api("/api/state");
  if (!meta || meta.version !== s.meta_version) await loadMeta();
  for (const [key, dev] of Object.entries(s.devices)) {
    const vals = dev.values || {};
    dev.values = (meta.devices[key] || []).map((p) => ({ ...p, value: vals[p.key] ?? null }));
  }
  s.controller.explain = meta.explain.dhw;
  s.heating.explain = meta.explain.heating;
  return s;
}

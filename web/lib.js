// Shared helpers: API access, DOM building, number formatting.

export async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

/** el("div", {class: "x", onclick}, child, "text", ...) — text always via textContent. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

const nf = (digits = 0) => new Intl.NumberFormat("de-DE", {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});

export function fmt(value, digits = 0, unit = "") {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  if (typeof value !== "number") return String(value);
  return nf(digits).format(value) + (unit ? ` ${unit}` : "");
}

/** Watts as W below 1 kW, else kW with one decimal. */
export function power(w) {
  if (w === null || w === undefined) return { v: "–", u: "" };
  if (Math.abs(w) < 0.5) w = 0;                       // no "-0 W" from tiny negative readings
  return Math.abs(w) < 1000 ? { v: nf(0).format(w), u: "W" } : { v: nf(2).format(w / 1000), u: "kW" };
}

export const time = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
export const dayTime = (iso) => new Date(iso).toLocaleString("de-DE", {
  weekday: "short", hour: "2-digit", minute: "2-digit",
});
/** With the date, for chart ranges longer than two days. */
export const dateTime = (iso) => new Date(iso).toLocaleString("de-DE", {
  weekday: "short", day: "numeric", month: "numeric", year: "2-digit", hour: "2-digit", minute: "2-digit",
});

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter(Boolean));
  return node;
}

/** German decimal commas in server-made texts ("46.1 °C" -> "46,1 °C"). */
export const deNum = (s) => String(s).replace(/(\d)\.(\d)/g, "$1,$2");

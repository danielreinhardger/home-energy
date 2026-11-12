// Page texts: the "web.*" keys of the server's catalog (homeenergy/lang/<code>.json), sent once
// with /api/meta in the installation's language. Everything the pages say goes through t():
//   t("web.overview.updated", { time: "19:07" })   → "updated 19:07"
// Static texts in index.html carry data-i18n / data-i18n-aria-label and are filled by applyStatic().
// Numbers and dates use locale() (lib.js), so they follow the language too.

const CACHE = "texts";          // the last texts, for a page opened while the server is unreachable
let texts = {};
let currentLocale = "en-GB";
try {
  const cached = JSON.parse(localStorage.getItem(CACHE) || "null");
  if (cached) ({ texts, locale: currentLocale } = cached);
} catch {}

/** Take the texts and locale from /api/meta and redraw the static parts of the page. */
export function setTexts(meta) {
  texts = meta.texts || {};
  currentLocale = meta.locale || currentLocale;
  document.documentElement.lang = meta.language || "en";
  try { localStorage.setItem(CACHE, JSON.stringify({ texts, locale: currentLocale })); } catch {}
  applyStatic();
}

export const locale = () => currentLocale;

/** The text for `key` with its {placeholders} filled from `args`. A missing key shows as ⟦key⟧. */
export function t(key, args) {
  const text = texts[key];
  if (text === undefined) {
    console.warn("missing text", key);
    return `⟦${key}⟧`;
  }
  return args ? text.replace(/\{(\w+)\}/g, (m, name) => (name in args ? String(args[name]) : m)) : text;
}

export function applyStatic(root = document) {
  if (!Object.keys(texts).length) return;           // nothing loaded yet (first visit): wait for setTexts
  for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of root.querySelectorAll("[data-i18n-aria-label]")) node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
}

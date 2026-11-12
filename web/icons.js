// Small stroke icons (24×24, currentColor) drawn for this dashboard. No dependencies.
import { t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";

const CLOUD_HI = "M7 14h10a3.6 3.6 0 0 0 .4-7.2A5.5 5.5 0 0 0 6.6 7.6 3.2 3.2 0 0 0 7 14z";
const SHAPES = {
  // weather
  sun: ["circle:12,12,4", "M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"],
  moon: ["M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"],
  partly_night: ["M11.5 9.6A4.2 4.2 0 0 1 6.3 3.6a4.6 4.6 0 1 0 5.2 6z",
    "M9 20h8.5a3.5 3.5 0 0 0 .3-7 5.2 5.2 0 0 0-9.9.9A3.1 3.1 0 0 0 9 20z"],
  partly: ["circle:8,8,3", "M8 2.5v1.5M2.5 8H4M4.1 4.1l1 1M11.9 4.1l-1 1",
    "M9 20h8.5a3.5 3.5 0 0 0 .3-7 5.2 5.2 0 0 0-9.9.9A3.1 3.1 0 0 0 9 20z"],
  cloud: ["M6.5 19h11a4 4 0 0 0 .5-8 6 6 0 0 0-11.6 1A3.5 3.5 0 0 0 6.5 19z"],
  fog: [CLOUD_HI, "M4 17.5h16M6 21h12"],
  drizzle: [CLOUD_HI, "M8 18v.5M12 18v.5M16 18v.5M10 21v.5M14 21v.5"],
  rain: [CLOUD_HI, "M8.5 17l-1.5 4M12.5 17l-1.5 4M16.5 17l-1.5 4"],
  snow: [CLOUD_HI, "M8 18.5h.01M12 18.5h.01M16 18.5h.01M10 21.5h.01M14 21.5h.01"],
  thunder: [CLOUD_HI, "M13 15l-3 4h4l-3 4"],
  // devices
  grid: ["M12 2.5 7 21.5M12 2.5l5 19M4.5 7h15M6.5 12h11M8.6 16.8l6.8-4.8M15.4 16.8 8.6 12"],
  meter: ["circle:12,13,8", "M12 13l3.5-3.5M8 6.5l.6 1M16 6.5l-.6 1M4.8 12h1.2M18 12h1.2"],
  inverter: ["rect:3.5,4.5,17,15,2.5", "M6.5 12c1.2-2.7 2.5-2.7 3.7 0s2.5 2.7 3.7 0 2.5-2.7 3.6 0"],
  pv: ["M3.5 15.5 6.5 5h14l-3 10.5z", "M5 10.2h14M11 5l-2.5 10.5M15.8 5l-2.6 10.5", "M10.5 15.5v4M7.5 19.5h6"],
  battery: ["rect:2.5,7,16,10,2", "M21 10.5v3", "M6 10.5v3M9.5 10.5v3M13 10.5v3"],
  charger: ["M4.5 15.5h15v-3.2l-1.8-4.3a1.5 1.5 0 0 0-1.4-1H7.7a1.5 1.5 0 0 0-1.4 1l-1.8 4.3z", "circle:8,16.5,1.8", "circle:16,16.5,1.8", "M4.5 12h15"],
  house: ["M3.5 11 12 4l8.5 7", "M5.5 9.5V20h13V9.5", "M10 20v-5h4v5"],
  heatpump: ["M12 3.5s5.5 6 5.5 10a5.5 5.5 0 0 1-11 0c0-4 5.5-10 5.5-10z", "M9.5 14.5a2.5 2.5 0 0 0 2.5 2.5"],
  close: ["M6 6l12 12M18 6 6 18"],
  bath: ["M3 12h18v2a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5z", "M6 12V6a2 2 0 0 1 3.7-1", "M7 19l-1 2M17 19l1 2"],
};

/** WMO weather code -> [icon, German description]; clear skies get a moon at night. */
export function weatherKind(code, night = false) {
  if (code === null || code === undefined) return ["cloud", t("web.weather.unknown")];
  if (code === 0) return [night ? "moon" : "sun", t("web.weather.clear")];
  if (code <= 2) return [night ? "partly_night" : "partly", code === 1 ? t("web.weather.mostly_clear") : t("web.weather.partly_cloudy")];
  if (code === 3) return ["cloud", t("web.weather.overcast")];
  if (code === 45 || code === 48) return ["fog", t("web.weather.fog")];
  if (code >= 51 && code <= 57) return ["drizzle", t("web.weather.drizzle")];
  if ((code >= 61 && code <= 67) || (code >= 80 && code <= 82)) return ["rain", t("web.weather.rain")];
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return ["snow", t("web.weather.snow")];
  if (code >= 95) return ["thunder", t("web.weather.thunder")];
  return ["cloud", t("web.weather.cloudy")];
}

/** An <svg> icon; works inside HTML and nested inside another SVG. */
export function icon(name, { size = 20, x, y, title, className = "icon" } = {}) {
  const svg = document.createElementNS(NS, "svg");
  const attrs = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor",
    "stroke-width": 1.75, "stroke-linecap": "round", "stroke-linejoin": "round", class: className,
    role: title ? "img" : "presentation" };
  if (x !== undefined) attrs.x = x;
  if (y !== undefined) attrs.y = y;
  for (const [k, v] of Object.entries(attrs)) svg.setAttribute(k, v);
  if (title) {
    const titleNode = document.createElementNS(NS, "title");
    titleNode.textContent = title;
    svg.append(titleNode);
  } else svg.setAttribute("aria-hidden", "true");
  for (const shape of SHAPES[name] || SHAPES.cloud) {
    let node;
    if (shape.startsWith("circle:")) {
      const [cx, cy, r] = shape.slice(7).split(",");
      node = document.createElementNS(NS, "circle");
      Object.entries({ cx, cy, r }).forEach(([k, v]) => node.setAttribute(k, v));
    } else if (shape.startsWith("rect:")) {
      const [rx, ry, w, h, rr] = shape.slice(5).split(",");
      node = document.createElementNS(NS, "rect");
      Object.entries({ x: rx, y: ry, width: w, height: h, rx: rr }).forEach(([k, v]) => node.setAttribute(k, v));
    } else {
      node = document.createElementNS(NS, "path");
      node.setAttribute("d", shape);
    }
    svg.append(node);
  }
  return svg;
}

export function weatherIcon(code, { night = false, ...opts } = {}) {
  const [kind, text] = weatherKind(code, night);
  return icon(kind, { title: text, ...opts });
}

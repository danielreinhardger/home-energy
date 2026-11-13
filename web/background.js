// The page background: the house as a small 3D model with light dots running along the cables in
// the direction power flows (from /api/state → flow), under the current weather (forecast, this
// hour) drawn comic-style. Scrolling swings the camera round the house.
// three.js is loaded on demand from a CDN; without it (offline) the page simply keeps its plain
// background. Setting: Display → animated background.
import { weatherKind } from "./icons.js";

const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.186.0/build/three.module.js";
const MAX_W = 8000;                     // fixed scale, so the dots' speed means the same all day
const FPS = 30;                         // enough for a background, half the GPU work

// weatherKind()'s icon names → what the sky does (0..1 each)
const SKIES = {
  sun:          { night: 0, cloud: .10, rain: 0,   snow: 0, fog: 0, thunder: 0 },
  moon:         { night: 1, cloud: .06, rain: 0,   snow: 0, fog: 0, thunder: 0 },
  partly:       { night: 0, cloud: .45, rain: 0,   snow: 0, fog: 0, thunder: 0 },
  partly_night: { night: 1, cloud: .45, rain: 0,   snow: 0, fog: 0, thunder: 0 },
  cloud:        { night: 0, cloud: 1,   rain: 0,   snow: 0, fog: 0, thunder: 0 },
  fog:          { night: 0, cloud: .7,  rain: 0,   snow: 0, fog: 1, thunder: 0 },
  drizzle:      { night: 0, cloud: .85, rain: .35, snow: 0, fog: 0, thunder: 0 },
  rain:         { night: 0, cloud: 1,   rain: 1,   snow: 0, fog: 0, thunder: 0 },
  snow:         { night: 0, cloud: .9,  rain: 0,   snow: 1, fog: 0, thunder: 0 },
  thunder:      { night: 0, cloud: 1,   rain: 1,   snow: 0, fog: 0, thunder: 1 },
};
const SKY_KEYS = ["night", "cloud", "rain", "snow", "fog", "thunder"];

// what the scene shows; updateBackground() fills it from each new state
const want = { sky: { ...SKIES.partly }, grid: 0, pv: 0, bat: 0, car: 0, house: 0, hp: 0, soc: null };
let stage = null, loading = false, failed = false;      // failed: no three.js or no WebGL – not tried again until reload

/** The weather of this hour (today's "now" part of the forecast); without a forecast: by the clock. */
function skyFrom(s) {
  const part = s.forecast?.summary?.days?.[0]?.parts?.[0];
  if (part && part.code !== null && part.code !== undefined) {
    const sky = SKIES[weatherKind(part.code, part.night)[0]] || SKIES.cloud;
    const cloud = { 1: .28, 2: .6 }[part.code] ?? sky.cloud;       // WMO 1 mostly clear, 2 partly cloudy: one icon, two amounts of cloud
    return { ...sky, cloud, night: part.night ? 1 : 0 };           // rain at night is still night
  }
  const hour = new Date(s.now).getHours();
  return { ...SKIES.partly, night: hour < 6 || hour >= 21 ? 1 : 0 };
}

/** Power per kind of node, W (several inverters or chargers add up). Signs as in the flow: grid + import, battery + charging. */
function powersFrom(flow) {
  const sum = {};
  for (const n of flow?.nodes || []) sum[n.kind] = (sum[n.kind] || 0) + (n.power_w || 0);
  return { grid: sum.grid || 0, pv: Math.max(0, sum.pv || 0), bat: sum.battery || 0, car: Math.max(0, sum.charger || 0),
           house: Math.max(0, sum.house || 0), hp: Math.max(0, sum.heatpump || 0) };
}

// Two switches: the installation's setting (Display → animated background, for everyone) and this
// device's own choice (the header button, kept in this browser), e.g. off on an old tablet.
const DEVICE_KEY = "background";
let allowed = true;
const deviceOff = () => { try { return localStorage.getItem(DEVICE_KEY) === "off"; } catch { return false; } };

/** Starts or stops the scene to match both switches, and shows the header button's state. */
function apply() {
  const on = allowed && !deviceOff();
  if (on && !stage && !loading && !failed) {
    loading = true;
    import(THREE_URL).then((THREE) => { stage = build(THREE); document.body.classList.add("has-bg"); })
      .catch((err) => { failed = true; console.warn("background not available:", err.message); })   // offline: plain page
      .finally(() => { loading = false; apply(); });               // switched off while it loaded? then stop again
  } else if (!on && stage) {
    stage.dispose(); stage = null; document.body.classList.remove("has-bg");
  }
  const btn = document.getElementById("bg-toggle");
  if (btn) { btn.hidden = !allowed || failed; btn.setAttribute("aria-pressed", on); }
}

/** The header button: this device's own on/off. */
export function setupBackground() {
  document.getElementById("bg-toggle")?.addEventListener("click", () => {
    try { deviceOff() ? localStorage.removeItem(DEVICE_KEY) : localStorage.setItem(DEVICE_KEY, "off"); } catch {}
    apply();
  });
}

/** Called with every new state: feeds the scene, and starts or stops it with the settings. */
export function updateBackground(s) {
  Object.assign(want, powersFrom(s.flow), { sky: skyFrom(s), soc: s.energy?.soc ?? null });
  allowed = s.background !== false;
  apply();
}

function build(THREE) {
  const canvas = document.getElementById("bg");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "low-power" });
  renderer.shadowMap.enabled = true;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = THREE.MathUtils.clamp, smooth = (a, b, x) => THREE.MathUtils.smoothstep(x, a, b);
  const hex3 = (h) => [(h >> 16 & 255) / 255, (h >> 8 & 255) / 255, (h & 255) / 255];
  const mix3 = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  const setC = (m, c) => m.color.setRGB(Math.min(c[0], 1), Math.min(c[1], 1), Math.min(c[2], 1), THREE.SRGBColorSpace);
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  const INK = 0x0b1020, NAVY = hex3(0x0c1829), BORDER = 0x22385a;
  const P = { ...want.sky, flash: 0 };                               // the sky as drawn: eased towards want.sky

  const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(28, 1, .5, 400);
  // sky: a banded gradient (flat comic inks), kept close to the Deep Navy theme; colours are sRGB in and out
  const sky = new THREE.ShaderMaterial({
    uniforms: { top: { value: new THREE.Vector3() }, bot: { value: new THREE.Vector3() } },
    vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform vec3 top; uniform vec3 bot; varying vec2 vUv;
      void main(){ float k = clamp((vUv.y - 0.44) / 0.36, 0.0, 1.0); k = floor(k * 7.0) / 6.0; gl_FragColor = vec4(mix(bot, top, clamp(k, 0.0, 1.0)), 1.0); }`,
    side: THREE.BackSide, depthTest: false, depthWrite: false });
  const dome = new THREE.Mesh(new THREE.SphereGeometry(150, 32, 24), sky); dome.renderOrder = -1; scene.add(dome);
  scene.fog = new THREE.FogExp2(0x0c1829, .004);
  const hemi = new THREE.HemisphereLight(0x9fc4ff, 0x0a1220, 1.0), sun = new THREE.DirectionalLight(0xfff1d0, 2.2);
  sun.position.set(-7, 10, 6); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0005;
  Object.assign(sun.shadow.camera, { left: -10, right: 10, top: 10, bottom: -10, near: 1, far: 50 });
  sun.shadow.autoUpdate = false; sun.shadow.needsUpdate = true;      // the site stands still: one shadow map, redone when the car comes or goes
  scene.add(hemi, sun);

  // ---- the site: house with PV roof, inverter, battery, meter cabinet, grid mast, wallbox with car, heat pump ----
  const std = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.85, ...o });
  const box = (w, h, d, mat, x, y, z, parent = scene) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z); m.castShadow = m.receiveShadow = true; parent.add(m); return m;
  };
  const plateMat = std(0x24406e), roofMat = std(0x2a3f66);
  box(13.9, 0.3, 9.4, plateMat, -0.55, -0.15, 0.1).castShadow = false;
  box(4.4, 2.4, 3.4, std(0x5d7fb5), 0, 1.2, -0.5);
  const tri = new THREE.Shape([new THREE.Vector2(-1.95, 0), new THREE.Vector2(1.95, 0), new THREE.Vector2(0, 1.3)]);
  const roof = new THREE.Mesh(new THREE.ExtrudeGeometry(tri, { depth: 4.8, bevelEnabled: false }).rotateY(Math.PI / 2), roofMat);
  roof.position.set(-2.4, 2.4, -0.5); roof.castShadow = roof.receiveShadow = true; scene.add(roof);
  const winMat = new THREE.MeshStandardMaterial({ color: 0x20324f, emissive: 0xffc27a, emissiveIntensity: 0.2 });
  for (const x of [0.3, 1.4]) box(0.7, 0.7, 0.04, winMat, x, 1.4, 1.2);
  box(0.04, 0.7, 0.7, winMat, -2.2, 1.5, -1.3);
  box(0.6, 1.15, 0.04, std(0x24385c), -0.6, 0.575, 1.2);
  const pvMat = new THREE.MeshStandardMaterial({ color: 0x143a72, roughness: 0.55, metalness: 0.1, emissive: 0xf2b52a, emissiveIntensity: 0 });
  for (const x of [-1.0, 1.0]) box(1.85, 0.06, 1.75, pvMat, x, 3.1, 0.5).rotation.x = Math.atan2(1.3, 1.95);
  box(0.14, 0.55, 0.42, std(0xd9e3f2), -2.27, 1.25, 0.2);
  box(0.5, 1.0, 0.8, std(0x2b4368), -2.6, 0.5, -1.0);
  const socBar = box(0.3, 0.8, 0.02, new THREE.MeshStandardMaterial({ color: 0x2cc79a, emissive: 0x2cc79a, emissiveIntensity: 0.9 }), -2.6, 0.5, -0.59);
  box(0.5, 0.9, 0.3, std(0x8fa5c4), -2.5, 0.45, 2.0);
  const pyl = std(0x8399b8);
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.16, 4.3, 6), pyl); mast.position.set(-6.3, 2.15, -3.2); mast.castShadow = true; scene.add(mast);
  box(0.08, 0.08, 1.7, pyl, -6.3, 3.7, -3.2); box(0.08, 0.08, 1.15, pyl, -6.3, 3.1, -3.2);
  box(0.25, 1.1, 0.2, std(0xd9e3f2), 2.6, 0.55, 3.3);
  const car = new THREE.Group(); scene.add(car);
  box(2.1, 0.5, 0.95, std(0x5aa7ff, { roughness: 0.4, metalness: 0.3 }), 4.4, 0.5, 3.3, car);
  box(1.15, 0.42, 0.85, std(0x16294a, { roughness: 0.2 }), 4.3, 0.95, 3.3, car);
  for (const [x, z] of [[3.7, 2.85], [5.1, 2.85], [3.7, 3.75], [5.1, 3.75]]) {
    const w = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, 0.14, 16).rotateX(Math.PI / 2), std(0x0b1322)); w.position.set(x, 0.24, z); car.add(w);
  }
  box(1.1, 0.9, 0.5, std(0xc9d5e8), 4.6, 0.45, -0.6);
  const disc = new THREE.Mesh(new THREE.CircleGeometry(0.36, 24), new THREE.MeshStandardMaterial({ color: 0x1a2740 })); disc.position.set(4.6, 0.47, -0.345); scene.add(disc);
  const fan = new THREE.Group(); fan.position.set(4.6, 0.47, -0.34); scene.add(fan);
  for (let i = 0; i < 3; i++) { const b = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.1, 0.02), std(0x8fa5c4)); b.rotation.z = i * Math.PI / 3; fan.add(b); }

  // ---- cables with running light dots ----
  function roundedPath(pts, r) {
    const path = new THREE.CurvePath();
    let prev = pts[0].clone();
    for (let i = 1; i < pts.length; i++) {
      const p = pts[i];
      if (i < pts.length - 1) {
        const d1 = p.clone().sub(pts[i - 1]), d2 = pts[i + 1].clone().sub(p);
        const rr = Math.min(r, d1.length() / 2, d2.length() / 2);
        const a = p.clone().addScaledVector(d1.normalize(), -rr), b = p.clone().addScaledVector(d2.normalize(), rr);
        path.add(new THREE.LineCurve3(prev, a)); path.add(new THREE.QuadraticBezierCurve3(a, p.clone(), b)); prev = b;
      } else path.add(new THREE.LineCurve3(prev, p.clone()));
    }
    return path;
  }
  const DOT = new THREE.SphereGeometry(1, 12, 8), M4 = new THREE.Matrix4();
  /** One cable; count, speed and size of its dots follow the power, their direction its sign. */
  function stream(pts, r = 0.25) {
    const path = roundedPath(pts, r), len = path.getLength(), lut = path.getSpacedPoints(Math.max(24, Math.ceil(len * 24)));
    const max = Math.ceil(len / 0.3) + 2, tubeMat = new THREE.MeshBasicMaterial({ color: BORDER });
    const dots = new THREE.InstancedMesh(DOT, new THREE.MeshBasicMaterial({ color: 0xbff1ff, fog: false }), max);
    const halo = new THREE.InstancedMesh(DOT, new THREE.MeshBasicMaterial({ color: 0x3fd0ff, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }), max);
    dots.frustumCulled = halo.frustumCulled = false; dots.count = halo.count = 0;
    scene.add(new THREE.Mesh(new THREE.TubeGeometry(path, lut.length, 0.035, 6), tubeMat), dots, halo);
    let n = 0, speed = 0, size = 0, forward = true, phase = Math.random();
    return {
      set(w) {                                                       // W, signed: + runs along the path
        const k = Math.abs(w) >= 20 ? Math.sqrt(Math.min(1, Math.abs(w) / MAX_W)) : 0;
        forward = w >= 0;
        n = k ? Math.max(2, Math.min(max, Math.round(len / (1.0 - 0.55 * k)))) : 0;
        speed = 0.7 + 2.4 * k; size = 0.075 * (0.75 + 0.6 * k);
        tubeMat.color.set(k ? 0x1f7399 : BORDER);
        dots.count = halo.count = n;
      },
      update(dt) {
        if (!n) return;
        phase = (phase + dt * speed / len) % 1;
        const L = lut.length - 1;
        for (let i = 0; i < n; i++) {
          let u = (i / n + phase) % 1;
          if (!forward) u = 1 - u;
          const f = u * L, i0 = Math.min(L - 1, Math.floor(f)), a = lut[i0], b = lut[i0 + 1], q = f - i0;
          const x = a.x + (b.x - a.x) * q, y = a.y + (b.y - a.y) * q, z = a.z + (b.z - a.z) * q;
          M4.makeScale(size, size, size).setPosition(x, y, z); dots.setMatrixAt(i, M4);
          M4.makeScale(size * 1.9, size * 1.9, size * 1.9).setPosition(x, y, z); halo.setMatrixAt(i, M4);
        }
        dots.instanceMatrix.needsUpdate = halo.instanceMatrix.needsUpdate = true;
      },
    };
  }
  const g = 0.04, hub = V(-2.5, g, 2.0);
  const cables = {
    grid: stream([V(-6.3, g, -3.2), V(-4.0, g, -3.2), V(-4.0, g, 2.0), hub]),                  // mast → meter (+ import)
    pv: stream([V(-1.2, 3.02, 0.62), V(-2.26, 3.02, 0.62), V(-2.3, 1.55, 0.2)], 0.15),         // roof → inverter
    bat: stream([V(-2.36, 1.0, 0.1), V(-2.42, 1.0, -0.9), V(-2.6, 1.02, -0.95)], 0.1),         // inverter → battery (+ charging)
    inv: stream([V(-2.36, 0.97, 0.3), V(-2.5, g, 0.55), hub]),                                 // inverter → meter
    car: stream([hub, V(-2.5, g, 3.3), V(2.6, g, 3.3)]),                                       // meter → wallbox
    house: stream([hub, V(-1.4, g, 2.0), V(-1.4, g, 1.26), V(-1.4, 0.9, 1.23)], 0.15),         // meter → house
    hp: stream([V(2.23, 0.45, -0.6), V(2.8, g, -0.6), V(4.05, g, -0.6)], 0.15),                // house → heat pump
  };

  // ---- comic weather around it: inked toon clouds, sun, moon, stars, rain, snow, lightning ----
  const grad = new THREE.DataTexture(new Uint8Array([95, 175, 255]), 3, 1, THREE.RedFormat);
  grad.minFilter = grad.magFilter = THREE.NearestFilter; grad.needsUpdate = true;
  const ink = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide }), SPH = new THREE.SphereGeometry(1, 28, 18);
  const ball = (parent, m, x, y, z, r, w) => { const me = new THREE.Mesh(SPH, m); me.position.set(x, y, z); me.scale.setScalar(r);
    const hull = new THREE.Mesh(SPH, ink); hull.scale.setScalar(1 + w / r); me.add(hull); parent.add(me); };   // inverted hull = outline
  const cloudMat = new THREE.MeshToonMaterial({ color: 0xffffff, gradientMap: grad }), clouds = [], NC = 24;
  // how many show follows the forecast's cloudiness: a couple when clear, about half when partly cloudy, all (and bigger) when overcast
  for (let i = 0; i < NC; i++) {
    const c = new THREE.Group();
    c.position.set(-24 + 48 * ((i * .618 + .2) % 1), 5.6 + 6.4 * ((i * .37) % 1), -20 + 28 * ((i * .71) % 1));
    c.userData = { i, y: c.position.y, sc: .8 + .7 * ((i * .53) % 1), speed: .2 + .25 * ((i * .29) % 1) };
    for (const [x, y, z, r] of [[0, 0, 0, 1.1], [-1.15, -.2, .2, .8], [1.2, -.15, -.1, .85], [.45, .45, .3, .75], [-.5, .4, -.3, .7]]) ball(c, cloudMat, x, y, z, r, .09);
    scene.add(c); clouds.push(c);
  }
  const noFog = (hex) => new THREE.MeshBasicMaterial({ color: hex, fog: false });
  const inkFar = new THREE.MeshBasicMaterial({ color: INK, side: THREE.BackSide, fog: false });
  const sunG = new THREE.Group(), rays = new THREE.Group(); sunG.position.set(10, 9, -18); sunG.add(rays); scene.add(sunG);
  const sunBall = new THREE.Mesh(SPH, noFog(0xffd23f)); sunBall.scale.setScalar(1.5);
  const sunHull = new THREE.Mesh(SPH, inkFar); sunHull.scale.setScalar(1.61); sunG.add(sunBall, sunHull);
  const rayGeo = new THREE.ConeGeometry(.34, .9, 4), rayMat = noFog(0xffa62b);
  for (let i = 0; i < 10; i++) { const a = i * Math.PI / 5, r = new THREE.Mesh(rayGeo, rayMat); r.position.set(Math.cos(a) * 2.4, Math.sin(a) * 2.4, 0); r.rotation.z = a - Math.PI / 2; rays.add(r); }
  const moon = new THREE.Group(); moon.position.set(3, 9.5, -20); scene.add(moon);
  const moonBall = new THREE.Mesh(SPH, noFog(0xf4f1d0)); moonBall.scale.setScalar(1.2);
  const moonHull = new THREE.Mesh(SPH, inkFar); moonHull.scale.setScalar(1.3); moon.add(moonBall, moonHull);
  const sp = [];
  for (let i = 0; i < 260; i++) { const a = Math.random() * Math.PI * 2, e = .08 + Math.random() * 1.1; sp.push(Math.cos(a) * Math.cos(e) * 130, Math.sin(e) * 130, Math.sin(a) * Math.cos(e) * 130); }
  const starGeo = new THREE.BufferGeometry(); starGeo.setAttribute("position", new THREE.Float32BufferAttribute(sp, 3));
  const starMat = new THREE.PointsMaterial({ color: 0xfff6c8, size: 3, sizeAttenuation: false, transparent: true, opacity: 0, fog: false, depthWrite: false });
  scene.add(new THREE.Points(starGeo, starMat));
  const seeds = (n) => Float32Array.from({ length: n * 3 }, () => Math.random()), D = new THREE.Object3D();
  const RN = 900, rs = seeds(RN), rain = new THREE.InstancedMesh(new THREE.BoxGeometry(.035, .7, .035), new THREE.MeshBasicMaterial({ color: 0xcfeaff }), RN);
  const SN = 700, ss = seeds(SN), snow = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), new THREE.MeshBasicMaterial({ color: 0xffffff }), SN);
  for (const m of [rain, snow]) { m.frustumCulled = false; m.count = 0; scene.add(m); }
  /** A zigzag as quads of width w. */
  function strokeGeo(pts, w) {
    const pos = [], h = w / 2;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      let dx = b[0] - a[0], dy = b[1] - a[1]; const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
      const nx = -dy * h, ny = dx * h, ex = dx * h, ey = dy * h;
      const A = [a[0] - ex + nx, a[1] - ey + ny], B = [a[0] - ex - nx, a[1] - ey - ny], C = [b[0] + ex + nx, b[1] + ey + ny], E = [b[0] + ex - nx, b[1] + ey - ny];
      pos.push(A[0], A[1], 0, B[0], B[1], 0, C[0], C[1], 0, C[0], C[1], 0, B[0], B[1], 0, E[0], E[1], 0);
    }
    const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); return geo;
  }
  const flat = (hex) => new THREE.MeshBasicMaterial({ color: hex, side: THREE.DoubleSide, fog: false });
  const bolt = new THREE.Group(), boltInk = new THREE.Mesh(new THREE.BufferGeometry(), flat(INK)), boltCore = new THREE.Mesh(new THREE.BufferGeometry(), flat(0xffe45c));
  boltCore.position.z = .05; boltInk.frustumCulled = boltCore.frustumCulled = false; bolt.visible = false; bolt.add(boltInk, boltCore); scene.add(bolt);
  function strike() {
    const pts = [], x = (Math.random() - .5) * 8;
    for (let i = 0; i <= 9; i++) { const k = i / 9; pts.push([x + (i % 2 ? 1 : -1) * (.25 + Math.random() * .7) * (1 - k * .5) + k * 1.2, 13 - 13 * k]); }
    boltInk.geometry.dispose(); boltCore.geometry.dispose(); boltInk.geometry = strokeGeo(pts, .5); boltCore.geometry = strokeGeo(pts, .24);
    bolt.position.set(4 + (Math.random() - .5) * 8, 1, -9); P.flash = 1;
  }

  // ---- frame ----
  let zoom = 1, clock = 0, scroll = 0, pvNow = 0, carOn = true, nextStrike = 3, last = 0;
  const tgt = new THREE.Vector3();
  function size() {
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight, false);
    cam.aspect = window.innerWidth / window.innerHeight; zoom = Math.max(1, .62 / cam.aspect);   // narrow screens: stay close to the house and let the plot's edges run off the sides cam.updateProjectionMatrix();
  }
  function frame(now) {
    if (now - last < 1000 / FPS - 2) return;                         // called at display rate, drawn at FPS
    const dt = Math.min((now - last) / 1000, .1), move = reduce ? 0 : dt; last = now;   // reduced motion: nothing moves by itself
    clock += move;
    const ease = 1 - Math.exp(-dt * 3);
    for (const key of SKY_KEYS) P[key] += (want.sky[key] - P[key]) * ease;
    if (want.sky.thunder > .5 && !reduce && (nextStrike -= dt) <= 0) { strike(); nextStrike = 4 + Math.random() * 6; }
    P.flash *= Math.exp(-dt * 3.2);                                  // one soft flash that fades, no strobe

    // scrolling swings the camera round the house and brings it down from the sky to the garden
    const room = document.documentElement.scrollHeight - window.innerHeight;
    scroll += ((room > 0 ? clamp(window.scrollY / room, 0, 1) : 0) - scroll) * (1 - Math.exp(-dt * 7));
    const e = scroll * scroll * (3 - 2 * scroll), az = -1.0 + .68 * e + Math.sin(clock * .15) * .015, el = .26 + .22 * e, dist = (33 - 9 * e) * zoom;
    tgt.set(-.4, 5.2 - 4.1 * e, .2);
    cam.position.set(tgt.x + Math.sin(az) * Math.cos(el) * dist, tgt.y + Math.sin(el) * dist, tgt.z + Math.cos(az) * Math.cos(el) * dist); cam.lookAt(tgt);

    // sky: clear → overcast, day → night, fog and lightning on top
    const o = smooth(.45, 1, P.cloud), day = 1 - P.night, fogC = mix3(hex3(0xb9c2cc), hex3(0x39424f), P.night);
    let top = mix3(mix3(hex3(0x2b7fdb), hex3(0x5f7088), o), mix3(hex3(0x0b1436), hex3(0x151b2b), o), P.night);
    let bot = mix3(mix3(hex3(0x9bdcff), hex3(0xa9b6c4), o), mix3(hex3(0x27408a), hex3(0x2a3345), o), P.night);
    top = mix3(mix3(top, fogC, P.fog * .7), NAVY, .45).map((v) => v + P.flash * .14);
    bot = mix3(mix3(bot, fogC, P.fog * .85), NAVY, .4).map((v) => v + P.flash * .14);
    sky.uniforms.top.value.fromArray(top); sky.uniforms.bot.value.fromArray(bot);
    scene.fog.color.setRGB(Math.min(bot[0], 1), Math.min(bot[1], 1), Math.min(bot[2], 1), THREE.SRGBColorSpace);
    scene.fog.density = .004 + P.fog * .028 + P.rain * .005 + P.snow * .007;

    // power flow
    cables.grid.set(want.grid); cables.pv.set(want.pv); cables.bat.set(want.bat); cables.inv.set(want.pv - want.bat);
    cables.car.set(want.car); cables.house.set(want.house); cables.hp.set(want.hp);
    for (const c of Object.values(cables)) c.update(move);
    const plugged = want.car >= 20;
    if (plugged !== carOn) { carOn = car.visible = plugged; sun.shadow.needsUpdate = true; }
    fan.rotation.z -= move * (want.hp >= 20 ? 5 + want.hp / 150 : 0);
    socBar.visible = want.soc !== null;
    const soc = clamp((want.soc ?? 0) / 100, .05, 1); socBar.scale.y = soc; socBar.position.y = .1 + .4 * soc;

    // light: the sun shines as the PV produces; windows glow when it doesn't
    pvNow += (Math.min(1, want.pv / 7000) - pvNow) * Math.min(1, dt * 6);
    sun.intensity = .25 + 2.5 * pvNow + P.flash * 2; sun.color.setRGB(.55 + .45 * pvNow, .68 + .27 * pvNow, 1 - .18 * pvNow);
    hemi.intensity = .55 + .6 * pvNow + .55 * day * P.cloud + P.flash;
    pvMat.emissiveIntensity = .5 * pvNow * (.85 + .15 * Math.sin(clock * 2));
    winMat.emissiveIntensity = (1 - pvNow) * (.25 + Math.min(1.3, want.house / 900));
    setC(plateMat, mix3(hex3(0x24406e), [.82, .88, .96], P.snow * .8)); setC(roofMat, mix3(hex3(0x2a3f66), [.86, .91, .97], P.snow * .85));

    // weather
    const dark = Math.max(P.rain * .8, P.thunder, smooth(.8, 1, P.cloud) * .45);
    let cc = mix3([1, 1, 1], [.5, .55, .63], dark);
    cc = mix3(cc, [cc[0] * .4, cc[1] * .45, cc[2] * .62], P.night * .3).map((v) => v + P.flash * .25);
    setC(cloudMat, cc);
    const open = 1 - smooth(.7, .9, P.cloud), sv = day * open, mv = P.night * open;
    sunG.visible = sv > .02; sunG.scale.setScalar(sv); sunG.quaternion.copy(cam.quaternion); rays.rotation.z = clock * .1;
    moon.visible = mv > .02; moon.scale.setScalar(mv);
    starMat.opacity = P.night * (1 - P.cloud * .9);
    const grow = smooth(.3, 1, P.cloud);                             // the cloudier, the bigger each cloud (up to about twice)
    for (const c of clouds) {
      const u = c.userData, v = clamp(P.cloud * (NC + 1.5) - u.i, 0, 1);
      c.position.x += move * u.speed; if (c.position.x > 26) c.position.x -= 52;
      c.visible = v > .01; c.scale.setScalar(u.sc * (.3 + .7 * v) * Math.min(1, v * 3) * (1 + .95 * grow));
      c.position.y = u.y + 2 * grow;                                 // bigger clouds ride higher, clear of the roof
    }
    const rn = Math.round(RN * P.rain); rain.count = rn; rain.visible = rn > 0;
    for (let i = 0; i < rn; i++) {
      const y = 15 - ((rs[i * 3 + 1] * 16 + clock * (15 + rs[i * 3 + 2] * 8)) % 16);
      D.position.set((rs[i * 3] - .5) * 34 + y * .12, y, 12 - ((i * .618) % 1) * 26); D.rotation.set(0, 0, -.12); D.scale.set(1, .45 + .55 * P.rain, 1); D.updateMatrix(); rain.setMatrixAt(i, D.matrix);
    }
    rain.instanceMatrix.needsUpdate = true;
    const sn = Math.round(SN * P.snow); snow.count = sn; snow.visible = sn > 0;
    for (let i = 0; i < sn; i++) {
      const y = 15 - ((ss[i * 3 + 1] * 16 + clock * (1.2 + ss[i * 3 + 2] * 1.1)) % 16);
      D.position.set((ss[i * 3] - .5) * 34 + Math.sin(clock * .7 + i) * .5, y, 12 - ((i * .618) % 1) * 26); D.rotation.set(0, 0, 0); D.scale.setScalar(.05 + ss[i * 3 + 2] * .05); D.updateMatrix(); snow.setMatrixAt(i, D.matrix);
    }
    snow.instanceMatrix.needsUpdate = true;
    bolt.quaternion.copy(cam.quaternion); bolt.visible = P.flash > .35;

    renderer.render(scene, cam);
  }

  window.addEventListener("resize", size);
  size();
  renderer.setAnimationLoop(frame);                                  // the browser stops calling it while the tab is hidden
  canvas.hidden = false;
  return {
    dispose() {
      renderer.setAnimationLoop(null); window.removeEventListener("resize", size); canvas.hidden = true;
      scene.traverse((obj) => {
        obj.geometry?.dispose();
        for (const m of [].concat(obj.material || [])) { for (const v of Object.values(m)) if (v?.isTexture) v.dispose(); m.dispose(); }
      });
      renderer.dispose();
    },
  };
}

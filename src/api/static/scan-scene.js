/* The landing page's 3D journey, rendered with three.js (vendored).
   ONE scene, ONE progress value: the page turns its scroll position into t in [0, 1], damps it,
   and everything here is a pure function of that t, so scrolling back plays the story backwards
   and the words (landing.js) and the pictures can never drift apart.
   The model: a full-body anatomy atlas (Z-Anatomy, CC-BY-SA; /static/atlas/CREDITS.txt) with a
   real, openly licensed head CT (/static/scan/CREDITS.txt) on its shoulders, sampled from this
   pipeline's GOLD output and lined up by the cranium. The camera follows a short keyframed path:
   body, then the head for de-identification, one turn to profile for the face cut, then back out
   to the whole body as its systems light up. No idle spinning. */

import * as THREE from "/static/vendor/three.module.min.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => { const t = clamp01(x); return t * t * (3 - 2 * t); };
const span = (p, a, b) => smooth((p - a) / (b - a)); // 0 before a, 1 after b

/* ---- data: one fetch, shared by every scene on the page ---- */
function loadAtlas() {
  return Promise.all([
    fetch("/static/atlas/atlas_points.json").then((r) => r.json()),
    fetch("/static/atlas/atlas_points.bin").then((r) => r.arrayBuffer()),
  ]).then(([meta, buf]) => {
    const n = meta.count;
    const q = new Int16Array(buf, 0, n * 3);
    const qn = new Int8Array(buf, n * 6, n * 3);
    const sys = new Uint8Array(buf, n * 9, n);
    const k = meta.extent_m / 32767;
    const pos = new Float32Array(n * 3), nrm = new Float32Array(n * 3), aSys = new Float32Array(n);
    for (let i = 0; i < n * 3; i++) { pos[i] = q[i] * k; nrm[i] = qn[i] / 127; }
    for (let i = 0; i < n; i++) aSys[i] = sys[i];
    return { meta, pos, nrm, sys: aSys, lo: meta.bounds_m[0], hi: meta.bounds_m[1] };
  });
}

let dataPromise = null;
export function loadScan() {
  if (!dataPromise) {
    dataPromise = Promise.all([
      fetch("/static/scan/head_points.json").then((r) => r.json()),
      fetch("/static/scan/head_points.bin").then((r) => r.arrayBuffer()),
      loadAtlas(),
    ]).then(([meta, buf, atlas]) => {
      const n = meta.count;
      const q = new Int16Array(buf, 0, n * 3);
      const qn = new Int8Array(buf, n * 6, n * 3);
      const tissue = new Uint8Array(buf, n * 9, n);
      const slice = new Uint16Array(buf, n * 10, n);
      const k = meta.extent_m / 32767;
      const layers = [0, 1].map((id) => {
        let m = 0;
        for (let i = 0; i < n; i++) if (tissue[i] === id) m++;
        return { pos: new Float32Array(m * 3), nrm: new Float32Array(m * 3), slice: new Float32Array(m), rand: new Float32Array(m), m: 0 };
      });
      let s = 0x2f6b52;
      let front = 0; // the anterior-most skin point: where S7's cut left the surface
      let neck = 0;  // the lowest point of the scan: below it, the atlas carries on
      for (let i = 0; i < n; i++) {
        const L = layers[tissue[i]];
        const j = L.m++;
        for (let c = 0; c < 3; c++) {
          L.pos[j * 3 + c] = q[i * 3 + c] * k;
          L.nrm[j * 3 + c] = qn[i * 3 + c] / 127;
        }
        L.slice[j] = slice[i];
        s = (s + 0x6d2b79f5) | 0; // mulberry32: deterministic sparkle / stagger per point
        let t = Math.imul(s ^ (s >>> 15), 1 | s);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        L.rand[j] = ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        if (tissue[i] === 0) front = Math.min(front, L.pos[j * 3 + 2]);
        neck = Math.min(neck, L.pos[j * 3 + 1]);
      }
      return { meta, skin: layers[0], bone: layers[1], front, neck, atlas };
    });
  }
  return dataPromise;
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch (e) { return false; }
}

const VERT = /* glsl */ `
  attribute vec3 aNrm;
  attribute float aSlice;
  attribute float aRand;
  uniform float uLayer; // 0 skin (glass), 1 bone (lit)
  uniform float uTime, uSize, uPixelRatio;
  uniform float uArrive, uSpread, uFuzz, uTissueMix, uSkinA, uBoneA, uScan, uScanOn;
  uniform float uCutZ, uCutOn, uHeadIn;
  uniform vec3 uNormal;
  uniform float uMidSlice, uSlices, uGap;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec3 p = position;
    p += uNormal * (aSlice - uMidSlice) * uGap * uSpread;
    float t = clamp(uArrive * (uSlices + 10.0) - aSlice, 0.0, 10.0) / 10.0;
    float arrived = smoothstep(0.0, 1.0, t);
    p += uNormal * (1.0 - arrived) * 0.16;
    vec3 j = normalize(vec3(fract(aRand * 12.9898), fract(aRand * 78.233), fract(aRand * 37.719)) - 0.5 + 1e-4);
    p += j * uFuzz * 0.004 + uNormal * (fract(aRand * 91.7) - 0.5) * uFuzz * 0.003;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;

    vec3 n = normalize(normalMatrix * aNrm);
    vec3 v = normalize(-mv.xyz);
    float facing = dot(n, v);
    n *= sign(facing + 1e-4);                         // light both sides of thin shells
    float key = max(dot(n, normalize(vec3(-0.45, 0.6, 0.66))), 0.0);
    float fill = max(dot(n, normalize(vec3(0.7, -0.15, 0.45))), 0.0);
    float rim = pow(1.0 - abs(facing), 2.2);

    float d = (dot(p, uNormal) - uScan) / 0.007;
    float scan = uScanOn * exp(-d * d);
    float c = (p.z - uCutZ) / 0.004;
    float sear = uCutOn * exp(-c * c);

    vec3 mint = vec3(0.79, 0.95, 0.875);
    vec3 coral = vec3(1.0, 0.45, 0.36);
    vec3 raw = mint * (0.55 + 0.45 * key);
    vec3 col;
    float a;
    if (uLayer > 0.5) {
      vec3 ivory = vec3(0.95, 0.92, 0.85);
      vec3 shade = vec3(0.13, 0.27, 0.23);
      vec3 lit = mix(shade, ivory, clamp(0.12 + 0.78 * key + 0.28 * fill, 0.0, 1.0)) + rim * mint * 0.35;
      col = mix(raw, lit, uTissueMix);
      a = uBoneA;
    } else {
      col = mix(mint, vec3(0.45, 0.86, 0.7), uTissueMix);
      a = uSkinA * (0.08 + 1.4 * rim);
    }
    vColor = col + scan * vec3(0.5, 1.0, 0.75) + sear * coral * 1.6;
    // while in slices, alternate bright and dim slices so the stack reads without stretching
    float band = mix(1.0, 0.3 + 0.7 * step(0.5, fract(aSlice * 0.5)), clamp(uSpread * 1.4, 0.0, 1.0));
    vAlpha = (arrived * a + (scan * 0.5 + sear) * arrived) * band * uHeadIn;
    gl_PointSize = uSize * uPixelRatio * (uLayer > 0.5 ? 1.0 : 1.15) * (1.0 + scan * 1.2 + sear * 1.5) / -mv.z;
  }`;

const FRAG = /* glsl */ `
  uniform float uLayer;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = dot(c, c);
    if (d > 0.25) discard;
    float edge = uLayer > 0.5 ? smoothstep(0.25, 0.16, d) : smoothstep(0.25, 0.0, d);
    gl_FragColor = vec4(vColor, vAlpha * edge);
  }`;

/* the full-body atlas: lit points, one colour per system; its head dissolves into the real CT */
const ATLAS_VERT = /* glsl */ `
  attribute vec3 aNrm;
  attribute float aSys;
  uniform float uAtlasSize, uPixelRatio, uAtlasA, uHeadFade, uNeckY, uReveal, uLo, uHi;
  uniform vec3 uSysA;
  uniform vec3 uGlow; // chapter 4: each system lights up in turn
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    vec3 n = normalize(normalMatrix * aNrm);
    vec3 v = normalize(-mv.xyz);
    float facing = dot(n, v);
    n *= sign(facing + 1e-4);
    float key = max(dot(n, normalize(vec3(-0.45, 0.6, 0.66))), 0.0);
    float fill = max(dot(n, normalize(vec3(0.7, -0.15, 0.45))), 0.0);
    float rim = pow(1.0 - abs(facing), 2.2);
    vec3 base = aSys < 0.5 ? vec3(0.95, 0.92, 0.85) : aSys < 1.5 ? vec3(0.98, 0.47, 0.5) : vec3(0.6, 0.96, 0.8);
    vec3 shade = vec3(0.1, 0.24, 0.2);
    float sysA = aSys < 0.5 ? uSysA.x : aSys < 1.5 ? uSysA.y : uSysA.z;
    float glow = aSys < 0.5 ? uGlow.x : aSys < 1.5 ? uGlow.y : uGlow.z;
    float h = (position.y - uLo) / (uHi - uLo);
    float ahead = step(uReveal, h);                              // not yet reached by the scan line
    float dl = (h - uReveal) / 0.012;
    float line = uReveal < 1.0 ? exp(-dl * dl) : 0.0;
    float head = smoothstep(uNeckY - 0.03, uNeckY + 0.03, position.y);
    vColor = mix(shade, base, clamp(0.15 + 0.75 * key + 0.3 * fill, 0.0, 1.0)) + rim * vec3(0.79, 0.95, 0.875) * 0.3
      + line * vec3(0.5, 1.0, 0.75) + glow * base * 0.75;
    vAlpha = min(1.0, (uAtlasA + glow * 0.7) * sysA * (1.0 - uHeadFade * head) * (1.0 - ahead) * 0.95);
    gl_PointSize = min(uAtlasSize * uPixelRatio / -mv.z, 2.6 * uPixelRatio) * (1.0 + line + glow * 0.5);
  }`;

/* S7: the cutting sheet (gradient body, fine scanlines, a coral leading edge) */
const SHEET_FRAG = /* glsl */ `
  uniform float uA;
  varying vec2 vUv;
  void main() {
    vec2 q = abs(vUv - 0.5) * 2.0;
    float e = max(q.x, q.y);
    float body = (1.0 - smoothstep(0.55, 1.0, e)) * (0.10 + 0.05 * step(0.5, fract(vUv.y * 90.0)));
    float rim = smoothstep(0.955, 0.985, e) * (1.0 - smoothstep(0.985, 1.0, e));
    vec3 col = mix(vec3(0.79, 0.95, 0.875), vec3(1.0, 0.45, 0.36), rim);
    gl_FragColor = vec4(col, uA * (body + rim * 0.9));
  }`;
const UV_VERT = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

/* S7: the removed region as a soft coral haze (no edges): dense at the cut, fading forward */
const HAZE_VERT = /* glsl */ `
  varying vec3 vP;
  void main() { vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const HAZE_FRAG = /* glsl */ `
  uniform float uA, uDepth, uHalfY, uHalfX;
  varying vec3 vP;
  void main() {
    float t = clamp((uDepth * 0.5 - vP.z) / uDepth, 0.0, 1.0);   // 0 at the cut, 1 at the far side
    float fade = pow(1.0 - t, 1.6);
    float soft = (1.0 - smoothstep(uHalfY * 0.55, uHalfY, abs(vP.y))) * (1.0 - smoothstep(uHalfX * 0.55, uHalfX, abs(vP.x)));
    float hatch = 0.6 + 0.4 * step(0.5, fract((vP.y + vP.z) * 260.0));
    gl_FragColor = vec4(1.0, 0.48, 0.38, uA * 0.16 * fade * soft * hatch);
  }`;

/* ---- the journey: one progress value t in [0, 1] drives everything ------------------------------ */

/* chapter windows (landing.js uses the same table for the captions and the stage ribbon) */
export const CHAPTERS = [
  { key: "hero", from: 0.0, to: 0.1 },
  { key: "received", from: 0.1, to: 0.28, stages: ["S1", "S2", "S3"] },
  { key: "names", from: 0.28, to: 0.46, stages: ["S4", "S5"] },
  { key: "face", from: 0.46, to: 0.64, stages: ["S6", "S7"] },
  { key: "named", from: 0.64, to: 0.84, stages: ["S8", "S9", "S10", "S11"] },
  { key: "served", from: 0.84, to: 1.0, stages: ["S12"] },
];

/* camera keyframes, in the body's own frame (metres, y up, the head at the origin).
   yaw 0 faces the viewer; the only real turn is to profile for the face cut (chapter 3). */
const HEAD = "head", BODY = "body";
const KEYS = [
  { t: 0.0, at: BODY, yaw: 0.0, pitch: 0.0 },
  { t: 0.08, at: BODY, yaw: 0.0, pitch: 0.0 },
  { t: 0.2, at: { y: -0.08, d: 1.1 }, yaw: -0.18, pitch: 0.02 },
  { t: 0.3, at: HEAD, yaw: -0.38, pitch: 0.04 },
  { t: 0.47, at: HEAD, yaw: -0.38, pitch: 0.04 },
  { t: 0.53, at: HEAD, yaw: -1.32, pitch: 0.0, zoom: 1.04 },
  { t: 0.61, at: HEAD, yaw: -1.32, pitch: 0.0, zoom: 1.04 },
  { t: 0.67, at: HEAD, yaw: -0.4, pitch: 0.03 },
  { t: 0.8, at: BODY, yaw: -0.12, pitch: 0.0 },
  { t: 0.9, at: BODY, yaw: 0.0, pitch: 0.0 },
  { t: 1.0, at: BODY, yaw: 0.0, pitch: 0.0 },
];

export class ScanScene {
  constructor(canvas, data) {
    this.canvas = canvas;
    // where the model sits on screen (fraction of the view): right half on desktop, upper part on
    // phones. One framing for the whole journey: the model never jumps from side to side.
    this.frame = [0.22, 0];
    this.bodyZoom = 1; this.heroDim = 1;
    this.t = 0;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.01, 12);
    this.head = new THREE.Group(); // the whole model; the real CT head sits at its origin
    this.scene.add(this.head);

    const { meta } = data;
    this.u = {
      uTime: { value: 0 }, uSize: { value: 1.05 }, uPixelRatio: { value: this.renderer.getPixelRatio() },
      uArrive: { value: 1 }, uSpread: { value: 0 }, uFuzz: { value: 0 }, uTissueMix: { value: 0 },
      uSkinA: { value: 0.55 }, uBoneA: { value: 0.95 }, uScan: { value: 0 }, uScanOn: { value: 0 },
      uCutZ: { value: 0 }, uCutOn: { value: 0 }, uHeadIn: { value: 0 },
      uNormal: { value: new THREE.Vector3(...meta.slice_normal_gltf).normalize() },
      uMidSlice: { value: (meta.slices - 1) / 2 }, uSlices: { value: meta.slices }, uGap: { value: 0.001 },
    };
    const layer = (L, id, material) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(L.pos, 3));
      g.setAttribute("aNrm", new THREE.BufferAttribute(L.nrm, 3));
      g.setAttribute("aSlice", new THREE.BufferAttribute(L.slice, 1));
      g.setAttribute("aRand", new THREE.BufferAttribute(L.rand, 1));
      const m = new THREE.ShaderMaterial({ uniforms: { ...this.u, uLayer: { value: id } }, vertexShader: VERT, fragmentShader: FRAG, ...material });
      const pts = new THREE.Points(g, m);
      this.head.add(pts);
      return pts;
    };
    this.bone = layer(data.bone, 1, { transparent: true, depthWrite: true, blending: THREE.NormalBlending });
    this.skin = layer(data.skin, 0, { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    this.skin.renderOrder = 1;
    this.front = data.front;
    this.#atlas(data);
    this.#props();

    this.dragYaw = 0; this.dragPitch = 0; this.dragging = false;
    this.#bindPointer();
    this.visible = false;
    this.clock = new THREE.Clock();
    new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; if (this.visible) this.#loop(); })
      .observe(canvas);
    new ResizeObserver(() => this.#resize()).observe(canvas);
    this.#resize();
  }

  #atlas(data) {
    const A = data.atlas;
    this.body = { cy: (A.lo[1] + A.hi[1]) / 2, height: A.hi[1] - A.lo[1] };
    this.sysA = [1, 1, 1];
    this.sysTarget = [1, 1, 1];
    this.au = {
      uAtlasSize: { value: 5.5 }, uPixelRatio: this.u.uPixelRatio, uAtlasA: { value: 1 },
      uHeadFade: { value: 0 }, uNeckY: { value: data.neck + 0.02 }, uReveal: { value: 1 },
      uLo: { value: A.lo[1] }, uHi: { value: A.hi[1] }, uSysA: { value: new THREE.Vector3(1, 1, 1) },
      uGlow: { value: new THREE.Vector3(0, 0, 0) },
      uLayer: { value: 1 },
    };
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(A.pos, 3));
    g.setAttribute("aNrm", new THREE.BufferAttribute(A.nrm, 3));
    g.setAttribute("aSys", new THREE.BufferAttribute(A.sys, 1));
    const m = new THREE.ShaderMaterial({ uniforms: this.au, vertexShader: ATLAS_VERT, fragmentShader: FRAG, transparent: true, depthWrite: true });
    this.atlas = new THREE.Points(g, m);
    this.head.add(this.atlas);
  }

  #props() {
    // S4: a small block of "header tag" glyphs in the gap between the words and the head,
    // dissolved during de-identification
    const tags = [];
    for (let r = 0; r < 10; r++) for (let c = 0; c < 9; c++) {
      if ((r * 7 + c * 3) % 5 === 0) continue; // ragged rows read as text
      tags.push(-0.144 + c * 0.0054, 0.055 - r * 0.0105, 0.0);
    }
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.Float32BufferAttribute(tags, 3));
    this.tagMat = new THREE.PointsMaterial({ color: 0xc9f2df, size: 0.0045, transparent: true, opacity: 0, depthWrite: false });
    this.tags = new THREE.Points(tg, this.tagMat);
    this.scene.add(this.tags);

    // S7: a cutting sheet that sweeps to the cut, and a soft haze where the face was
    this.sheetMat = new THREE.ShaderMaterial({ uniforms: { uA: { value: 0 } }, vertexShader: UV_VERT, fragmentShader: SHEET_FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
    this.sheet = new THREE.Mesh(new THREE.PlaneGeometry(0.2, 0.24), this.sheetMat);
    this.sheet.renderOrder = 2;
    this.head.add(this.sheet);
    const depth = 0.05, hy = 0.1, hx = 0.085;
    this.hazeMat = new THREE.ShaderMaterial({
      uniforms: { uA: { value: 0 }, uDepth: { value: depth }, uHalfY: { value: hy }, uHalfX: { value: hx } },
      vertexShader: HAZE_VERT, fragmentShader: HAZE_FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide,
    });
    this.haze = new THREE.Group();
    for (let i = 0; i < 7; i++) {
      const slab = new THREE.Mesh(new THREE.BoxGeometry(hx * 2 * (1 - i * 0.04), hy * 2 * (1 - i * 0.04), depth), this.hazeMat);
      slab.renderOrder = 2;
      this.haze.add(slab);
    }
    this.haze.position.z = this.front - depth / 2;
    this.head.add(this.haze);
  }

  #bindPointer() {
    // drag to peek (hero and between beats); the camera springs back to its path on release
    const c = this.canvas;
    let down = null;
    c.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, yaw: this.dragYaw, pitch: this.dragPitch }; this.dragging = true; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointermove", (e) => {
      if (!down) return;
      this.dragYaw = Math.max(-1.2, Math.min(1.2, down.yaw + (e.clientX - down.x) * 0.006));
      this.dragPitch = Math.max(-0.35, Math.min(0.35, down.pitch + (e.clientY - down.y) * 0.004));
    });
    const up = () => { down = null; this.dragging = false; };
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
  }

  #resize() {
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.visible || REDUCED) this.render();
  }

  /* hero chips: which atlas systems are shown (skeleton, vessels, nerves) */
  setSystems(on) {
    this.sysTarget = on.map((v) => (v ? 1 : 0));
    if (REDUCED) { this.sysA = [...this.sysTarget]; this.render(); }
  }

  /* hero: a scan line sweeps the atlas in from the feet, once */
  reveal(duration = 2.2) {
    if (REDUCED) { this.au.uReveal.value = 1; this.render(); return; }
    this.au.uReveal.value = 0;
    this.revealStart = performance.now();
    this.revealDur = duration * 1000;
  }

  /* camera pose at journey progress t: smoothstep between keyframes, distance in log space */
  #pose(t) {
    const fit = Math.max(1, 0.95 / Math.max(0.1, this.camera.aspect));
    const resolve = (at, zoom = 1) => {
      if (at === BODY) return { y: this.body.cy, d: this.body.height * 2.6 * this.bodyZoom };
      if (at === HEAD) return { y: 0.0, d: 0.66 * fit * zoom };
      return { y: at.y, d: at.d * fit };
    };
    let i = 0;
    while (i < KEYS.length - 2 && t > KEYS[i + 1].t) i++;
    const a = KEYS[i], b = KEYS[i + 1];
    const u = smooth((t - a.t) / Math.max(1e-6, b.t - a.t));
    const pa = resolve(a.at, a.zoom), pb = resolve(b.at, b.zoom);
    const yu = pb.d < pa.d ? 1 - (1 - u) ** 1.7 : u ** 1.7;
    return {
      y: pa.y + (pb.y - pa.y) * yu,
      d: Math.exp(Math.log(pa.d) + (Math.log(pb.d) - Math.log(pa.d)) * u),
      yaw: a.yaw + (b.yaw - a.yaw) * u,
      pitch: a.pitch + (b.pitch - a.pitch) * u,
    };
  }

  /* everything visual, as a pure function of the (damped) journey progress t */
  setProgress(t) {
    this.t = clamp01(t);
    t = this.t;
    const u = this.u, au = this.au;
    const hump = (a, b, c, d) => span(t, a, b) * (1 - span(t, c, d));

    // 1 · Received: the atlas head gives way to the real CT, which arrives as slices, then is ordered
    au.uHeadFade.value = span(t, 0.12, 0.2);
    u.uHeadIn.value = span(t, 0.13, 0.2);
    u.uArrive.value = span(t, 0.13, 0.22);
    u.uSpread.value = span(t, 0.12, 0.16) * (1 - 0.72 * span(t, 0.22, 0.27)) * (1 - span(t, 0.47, 0.52));
    // one scan plane sweeps the head while it arrives (S1), one verification sweep in chapter 2 (S5)
    const acq = hump(0.13, 0.14, 0.21, 0.22), ver = hump(0.38, 0.39, 0.44, 0.45);
    u.uScanOn.value = acq > 0.01 || ver > 0.01 ? Math.max(acq, ver) : 0;
    u.uScan.value = acq > 0.01 ? 0.12 - 0.26 * span(t, 0.13, 0.22) : -0.14 + 0.28 * span(t, 0.38, 0.45);

    // 2 · Names removed: the header tags appear beside the head, then dissolve upward
    this.tagMat.opacity = 0.85 * hump(0.28, 0.31, 0.34, 0.4);
    this.tags.position.y = 0.035 * span(t, 0.34, 0.4);

    // 3 · The face comes off: slices fuse into one volume (S6), the sheet sweeps to the cut (S7)
    u.uFuzz.value = hump(0.46, 0.49, 0.5, 0.53);
    const sweep = span(t, 0.53, 0.58);
    this.sheet.position.z = this.front - 0.09 * (1 - sweep);
    this.sheetMat.uniforms.uA.value = hump(0.52, 0.535, 0.6, 0.63);
    u.uCutZ.value = this.sheet.position.z;
    u.uCutOn.value = hump(0.565, 0.58, 0.63, 0.68);
    this.hazeMat.uniforms.uA.value = hump(0.575, 0.6, 0.66, 0.72);

    // raw single-colour data until segmentation (chapter 4), then bone and skin, lit
    u.uTissueMix.value = 1 - span(t, 0.12, 0.17) + span(t, 0.66, 0.72);

    // 4 · Every structure, named: the body returns, and its systems light up one after another
    const focus = hump(0.16, 0.26, 0.66, 0.74); // while the story is on the head, the body steps back
    au.uAtlasA.value = (1 - 0.72 * focus) * (this.heroDim + (1 - this.heroDim) * span(t, 0.02, 0.1));
    au.uGlow.value.set(hump(0.7, 0.72, 0.74, 0.77), hump(0.73, 0.75, 0.77, 0.8), hump(0.76, 0.78, 0.8, 0.83));
    if (REDUCED || !this.visible) this.render();
  }

  render() {
    const time = this.clock.getElapsedTime();
    this.u.uTime.value = REDUCED ? 0 : time;
    if (this.revealStart != null) {
      const r = clamp01((performance.now() - this.revealStart) / this.revealDur);
      this.au.uReveal.value = r < 1 ? smooth(r) * 1.02 : 1;
      if (r >= 1) this.revealStart = null;
    }
    for (let i = 0; i < 3; i++) this.sysA[i] += (this.sysTarget[i] - this.sysA[i]) * (REDUCED ? 1 : 0.12);
    this.au.uSysA.value.set(...this.sysA);

    const pose = this.#pose(this.t);
    // the hero breathes (a small sway, not a spin); the story holds still
    const sway = REDUCED ? 0 : 0.05 * Math.sin(time * 0.45) * (1 - span(this.t, 0.04, 0.1));
    if (!this.dragging) { this.dragYaw *= 0.9; this.dragPitch *= 0.9; } // spring back to the path
    this.head.rotation.set(pose.pitch + this.dragPitch, pose.yaw + sway + this.dragYaw + Math.PI, 0);
    this.camera.position.set(0, pose.y + 0.01, pose.d);
    this.camera.lookAt(0, pose.y, 0);

    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (w && h) this.camera.setViewOffset(w, h, -this.frame[0] * w, -this.frame[1] * h, w, h);
    this.atlas.visible = this.au.uAtlasA.value > 0.01;
    this.bone.visible = this.u.uHeadIn.value > 0.01;
    this.skin.visible = this.u.uHeadIn.value > 0.01;
    this.renderer.render(this.scene, this.camera);
    window.__journey = { t: this.t, yaw: this.head.rotation.y, y: pose.y, d: pose.d };
  }

  #loop() {
    if (this.running) return;
    this.running = true;
    let last = performance.now();
    const tick = (now) => {
      if (!this.visible || document.hidden) { this.running = false; return; }
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (this.onFrame) this.onFrame(dt);
      this.render();
      if (REDUCED && this.revealStart == null) { this.running = false; return; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}

export { REDUCED };

/* The landing page's 3D hall, rendered with three.js (vendored).
   ONE scene, ONE progress value: the page turns its scroll position into t in [0, 1], damps it,
   and everything here is a pure function of that t, so scrolling back plays the story backwards
   and the words (landing.js) and the pictures can never drift apart.

   The hall (columns, arches, platform, pedestal) is built from primitives, so the first frame
   needs no download. The statue is an anatomy atlas (Z-Anatomy, CC-BY-SA; /static/body/CREDITS.txt)
   as solid meshes. The story happens on the statue itself: the gantry ring scans it into slices,
   its identity is stripped and the face cut, the slices close up, the marble opens to the labelled
   systems inside, and a hand turns it. */

import * as THREE from "./vendor/three.module.min.js";

const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => { const t = clamp01(x); return t * t * (3 - 2 * t); };
const span = (p, a, b) => smooth((p - a) / (b - a)); // 0 before a, 1 after b
const hump = (p, a, b, c, d) => span(p, a, b) * (1 - span(p, c, d));
const lerp = (a, b, k) => a + (b - a) * k;

/* ---- palette (linear-ish sRGB hex; the page's tokens use the same values) ---- */
const INK = 0x060b09;      // the hall's green-black
const MINT = 0xc9f2df;     // the accent
const SYSTEM_COLOURS = { skeleton: 0xefe6d2, vessels: 0xc8504a, organs: 0xd99a80 };

/* ---- the story: chapter windows (landing.js uses the same table for the words) ---- */
export const CHAPTERS = [
  { key: "hero", from: 0.0, to: 0.1 },
  { key: "scan", from: 0.1, to: 0.3 },
  { key: "privacy", from: 0.3, to: 0.48 },
  { key: "ai", from: 0.48, to: 0.77 },
  { key: "hands", from: 0.77, to: 1.0 },
];

/* the hall, in metres: floor at y 0, the statue on its pedestal at the origin, the camera
   coming in from +Z through two arches */
const STEP_TOP = 0.24, PEDESTAL_TOP = 0.79;
const ARCHES = [5.0, 11.5];

/* camera keyframes: position, look-at, in world space. Pairs of equal keys are holds, where the
   words for a beat are on screen. */
const KEYS = [
  { t: 0.0, pos: [0, 1.95, 9.4], look: [0, 1.72, 0] },
  { t: 0.07, pos: [0, 1.95, 8.9], look: [0, 1.74, 0] },
  { t: 0.14, pos: [-1.9, 1.75, 4.9], look: [0, 1.62, 0] },
  { t: 0.21, pos: [-1.55, 1.85, 4.5], look: [0, 1.66, 0] },
  { t: 0.29, pos: [-1.25, 2.3, 2.7], look: [0.02, 2.2, 0] },
  { t: 0.36, pos: [-1.6, 2.56, 1.75], look: [0.02, 2.47, 0] },
  { t: 0.46, pos: [-1.55, 2.45, 1.75], look: [0.04, 2.36, 0] },
  { t: 0.53, pos: [2.3, 1.95, 5.3], look: [0, 1.62, 0] },
  { t: 0.64, pos: [0.5, 1.75, 4.7], look: [0, 1.6, 0] },
  { t: 0.75, pos: [-2.1, 1.85, 4.9], look: [0, 1.62, 0] },
  { t: 0.81, pos: [0.15, 2.15, 4.1], look: [-0.25, 1.98, 0] },
  { t: 0.93, pos: [0.1, 2.15, 4.0], look: [-0.25, 1.98, 0] },
  { t: 1.0, pos: [0, 2.35, 8.6], look: [0, 1.95, 0] },
];

/* the hand: 21 landmarks in MediaPipe's order (wrist, thumb 1-4, index 5-8, middle 9-12,
   ring 13-16, pinky 17-20), right hand, palm to the camera, in units of about 1.6 cm */
const HAND_EDGES = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11],
  [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [0, 17], [17, 18], [18, 19], [19, 20],
];
const HAND_OPEN = [
  [0, 0, 0], [-0.9, 0.5, 0.2], [-1.6, 1.1, 0.3], [-2.1, 1.7, 0.35], [-2.5, 2.2, 0.4],
  [-0.8, 2.4, 0], [-0.95, 3.5, 0], [-1.05, 4.2, 0], [-1.1, 4.8, 0],
  [0, 2.5, 0], [0, 3.7, 0], [0, 4.5, 0], [0, 5.1, 0],
  [0.75, 2.35, 0], [0.85, 3.4, 0], [0.9, 4.1, 0], [0.95, 4.6, 0],
  [1.4, 2.0, 0], [1.6, 2.8, 0], [1.75, 3.3, 0], [1.85, 3.75, 0],
];
const HAND_PINCH = [
  [0, 0, 0], [-0.8, 0.6, 0.3], [-1.3, 1.4, 0.6], [-1.45, 2.2, 0.8], [-1.35, 2.85, 0.9],
  [-0.8, 2.4, 0], [-1.05, 3.3, 0.4], [-1.25, 3.2, 0.85], [-1.35, 2.9, 0.95],
  [0, 2.5, 0], [0, 3.7, 0.05], [0, 4.5, 0.1], [0, 5.05, 0.15],
  [0.75, 2.35, 0], [0.85, 3.4, 0.05], [0.9, 4.1, 0.1], [0.95, 4.55, 0.15],
  [1.4, 2.0, 0], [1.6, 2.8, 0.05], [1.75, 3.3, 0.1], [1.85, 3.7, 0.15],
];
// pointing (index out, the rest curled): held on a part, it selects it (gestures v3)
const HAND_POINT = [
  [0, 0, 0], [-0.8, 0.6, 0.3], [-1.3, 1.3, 0.7], [-1.0, 1.9, 1.1], [-0.6, 2.2, 1.2],
  [-0.8, 2.4, 0], [-0.95, 3.5, 0], [-1.05, 4.2, 0], [-1.1, 4.8, 0],
  [0, 2.5, 0], [0, 3.1, 0.85], [0.05, 2.65, 1.35], [0.05, 2.1, 1.15],
  [0.75, 2.35, 0], [0.8, 2.9, 0.8], [0.8, 2.5, 1.25], [0.8, 2.05, 1.05],
  [1.4, 2.0, 0], [1.5, 2.5, 0.7], [1.45, 2.2, 1.05], [1.4, 1.85, 0.9],
];

/* ---- data ---------------------------------------------------------------------------------- */

let bodyPromise = null;
export function loadBody() {
  if (!bodyPromise) {
    bodyPromise = Promise.all([
      fetch(new URL("./body/body.json", import.meta.url)).then((r) => r.json()),
      fetch(new URL("./body/body.bin", import.meta.url)).then((r) => r.arrayBuffer()),
    ]).then(([meta, buf]) => ({
      meta,
      systems: meta.systems.map((s) => ({
        name: s.name,
        pos: new Int16Array(buf, s.pos_offset, s.vertices * 3),
        nrm: new Int8Array(buf, s.nrm_offset, s.vertices * 3),
        idx: new Uint16Array(buf, s.idx_offset, s.triangles * 3),
      })),
    }));
  }
  return bodyPromise;
}

export function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!c.getContext("webgl2");
  } catch (e) { return false; }
}

/* ---- shaders --------------------------------------------------------------------------------- */

const NOISE = /* glsl */ `
  float hash3(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float vnoise(vec3 x) {
    vec3 i = floor(x), f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash3(i), hash3(i + vec3(1, 0, 0)), f.x), mix(hash3(i + vec3(0, 1, 0)), hash3(i + vec3(1, 1, 0)), f.x), f.y),
               mix(mix(hash3(i + vec3(0, 0, 1)), hash3(i + vec3(1, 0, 1)), f.x), mix(hash3(i + vec3(0, 1, 1)), hash3(i + vec3(1, 1, 1)), f.x), f.y), f.z);
  }
  float fbm(vec3 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 4; i++) { s += a * vnoise(p); p *= 2.03; a *= 0.5; } return s; }`;

/* A standard / physical material that can dissolve (or materialise) from the head down, with a
   glowing seam, optional marble veining and a whole-system glow. Its object space is the statue's
   quantised frame: y runs from -1 (feet) to about 0.05 (vertex). */
const SLICES = 84.0; // the scan beat's slices across the statue's height (about 2 cm each)

function dissolving(material, { invert = false, veins = false, scan = false } = {}) {
  const u = {
    uCut: { value: invert ? 0 : 0 },
    uSeam: { value: new THREE.Color(MINT) },
    uGlow: { value: 0 },
    uGlowColor: { value: material.color.clone() },
    // scan mode (the marble): where the gantry is, how sliced, how spread, the face cut
    uScanY: { value: -2 }, uScanOn: { value: 0 }, uBands: { value: 0 }, uSpread: { value: 0 },
    uCutZ: { value: 0 }, uCutOn: { value: 0 }, uCutYMin: { value: 0 },
  };
  const S = SLICES.toFixed(1);
  material.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u);
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", `#include <common>
        varying vec3 vObj;
        ${scan ? "uniform float uSpread;" : ""}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>
        vObj = position;
        ${scan ? `// the slices part like a stack of plates, the feet staying on the pedestal
        transformed.y += floor((position.y + 1.0) * ${S}) * uSpread * 0.0032;` : ""}`);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>
        varying vec3 vObj;
        uniform float uCut, uGlow;
        uniform vec3 uSeam, uGlowColor;
        ${scan ? "uniform float uScanY, uScanOn, uBands, uCutZ, uCutOn, uCutYMin;" : ""}
        ${NOISE}`)
      .replace("#include <clipping_planes_fragment>", `#include <clipping_planes_fragment>
        // the noise only runs while the seam is moving; a settled system is plain (and cheap)
        float eDis = 0.0;
        if (uCut > 0.001 && uCut < 0.999) {
          float hDis = clamp((vObj.y + 1.0) / 1.05, 0.0, 1.0);
          float dDis = (1.0 - hDis) * 0.78 + fbm(vObj * 16.0) * 0.22;
          float cDis = uCut * 1.12 - 0.06;
          float sDis = ${invert ? "cDis - dDis" : "dDis - cDis"};
          if (sDis < 0.0) discard;
          eDis = 1.0 - smoothstep(0.0, 0.022, sDis);
        } else if (${invert ? "uCut <= 0.001" : "uCut >= 0.999"}) discard;
        ${scan ? `// below the gantry the marble has been read: a stack of slices with a gap between each
        float lScan = 0.0, eScan = 0.0, sear = 0.0;
        if (uBands > 0.001) {
          float fS = fract((vObj.y + 1.0) * ${S});
          float bS = uBands * step(vObj.y, uScanY);
          float gap = 1.0 - 0.2 * bS;
          if (fS > gap) discard;
          lScan = bS * smoothstep(gap - 0.09, gap, fS);
        }
        float dS = (vObj.y - uScanY) / 0.0035;
        eScan = uScanOn * exp(-dS * dS);
        float cS = (vObj.z - uCutZ) / 0.003;
        sear = uCutOn * exp(-cS * cS) * step(uCutYMin, vObj.y);` : ""}`)
      .replace("#include <color_fragment>", `#include <color_fragment>
        ${veins ? `float vein = smoothstep(0.86, 1.0, abs(sin(vObj.x * 38.0 + vObj.y * 9.0 + vnoise(vObj * 6.0) * 9.0)));
        diffuseColor.rgb *= 1.0 - vein * 0.16;` : ""}
        ${scan ? "diffuseColor.rgb *= 1.0 - 0.3 * uBands * step(vObj.y, uScanY);" : ""}`)
      .replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
        totalEmissiveRadiance += uSeam * eDis * 2.4 + uGlowColor * uGlow * 0.55;
        ${scan ? "totalEmissiveRadiance += uSeam * (lScan * 1.5 + eScan * 2.6) + vec3(1.0, 0.45, 0.36) * sear * 4.0;" : ""}`);
  };
  material.customProgramCacheKey = () => `vesalius-dissolve-${invert ? 1 : 0}-${veins ? 1 : 0}-${scan ? 1 : 0}`;
  return u;
}

/* S7: the cutting sheet (soft body, fine scanlines, a coral leading edge) */
const SHEET_VERT = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
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

/* a soft round glow on a plane (behind the statue, and under the orb) */
const GLOW_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uA;
  varying vec2 vUv;
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    gl_FragColor = vec4(uColor, uA * pow(max(0.0, 1.0 - d), 2.2));
  }`;

/* ---- the scene --------------------------------------------------------------------------------- */

export class HallScene {
  constructor(canvas) {
    this.canvas = canvas;
    // where the subject sits on screen (fraction of the view); set by the page for its layout
    this.frame = [0.18, 0];
    this.t = 0;
    this.ready = false;
    this.overlays = []; // { el, at: () => Vector3 world, alpha: () => number }

    const r = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    r.setClearColor(INK, 1);
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.05;
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFShadowMap;
    this.renderer = r;

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(INK, 0.06);
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.05, 60);
    this.#environment();
    this.#hall();
    this.#lights();

    // the statue's frame: the atlas and the CT share it (head at the origin, anterior -Z); half a
    // turn about Y faces it to the camera. A rotation, never a mirror.
    this.statue = new THREE.Group();
    this.statue.rotation.y = Math.PI;
    this.scene.add(this.statue);

    this.dragYaw = 0; this.dragPitch = 0; this.dragging = false;
    this.#bindPointer();
    this.visible = false;
    this.startedAt = performance.now();
    new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; if (this.visible) this.#loop(); })
      .observe(canvas);
    new ResizeObserver(() => this.#resize()).observe(canvas);
    this.#resize();
  }

  /* reflections for the marble and the glossy hall, from a tiny emissive room (core three only) */
  #environment() {
    const pm = new THREE.PMREMGenerator(this.renderer);
    const room = new THREE.Scene();
    const box = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10), new THREE.MeshBasicMaterial({ color: 0x050807, side: THREE.BackSide }));
    room.add(box);
    const panel = (w, h, color, k, pos, rot) => {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(k) }));
      m.position.set(...pos); m.rotation.set(...rot); room.add(m);
    };
    panel(4, 2, 0xffffff, 3.2, [0, 4.9, 0], [Math.PI / 2, 0, 0]);       // soft box overhead
    panel(1.2, 6, MINT, 1.6, [4.9, 0, -1], [0, -Math.PI / 2, 0]);        // green strip, right
    panel(1.2, 6, 0xffffff, 0.7, [-4.9, 0, 1], [0, Math.PI / 2, 0]);     // cool strip, left
    panel(6, 0.5, MINT, 0.8, [0, -1, -4.9], [0, 0, 0]);                  // a line behind
    this.scene.environment = pm.fromScene(room, 0.035).texture;
    this.scene.environmentIntensity = 0.9;
    pm.dispose();
  }

  #hall() {
    const s = this.scene;
    const stone = new THREE.MeshStandardMaterial({ color: 0x131d1a, roughness: 0.3, metalness: 0.15 });
    const floorMat = new THREE.MeshStandardMaterial({ color: 0x0a110f, roughness: 0.22, metalness: 0.2 });

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    s.add(floor);

    // concentric lines on the floor around the platform
    const lineMat = new THREE.MeshBasicMaterial({ color: MINT, transparent: true, opacity: 0.09, depthWrite: false, fog: true });
    for (const rad of [3.1, 4.4, 6.2]) {
      const ring = new THREE.Mesh(new THREE.RingGeometry(rad, rad + 0.012, 160), lineMat);
      ring.rotation.x = -Math.PI / 2; ring.position.y = 0.002;
      s.add(ring);
    }

    // stepped platform and pedestal
    const steps = [[2.7, 0.12, 0.06], [2.0, 0.12, 0.18]];
    for (const [rad, h, y] of steps) {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(rad, rad, h, 96), stone);
      m.position.y = y; m.receiveShadow = true; m.castShadow = false;
      s.add(m);
    }
    const ped = new THREE.Mesh(new THREE.CylinderGeometry(0.46, 0.5, PEDESTAL_TOP - STEP_TOP - 0.04, 96), stone);
    ped.position.y = STEP_TOP + (PEDESTAL_TOP - STEP_TOP - 0.04) / 2;
    ped.receiveShadow = true; ped.castShadow = true;
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.04, 96), stone);
    cap.position.y = PEDESTAL_TOP - 0.02; cap.receiveShadow = true;
    s.add(ped, cap);

    // fluted columns, two rows down the hall
    const colGeo = new THREE.CylinderGeometry(0.3, 0.3, 9, 96, 1, true);
    const p = colGeo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), z = p.getZ(i);
      const a = Math.atan2(z, x);
      const k = 1 - 0.07 * Math.pow(Math.abs(Math.cos(a * 12)), 0.6); // 24 flutes
      p.setX(i, x * k); p.setZ(i, z * k);
    }
    colGeo.computeVertexNormals();
    const cols = new THREE.InstancedMesh(colGeo, stone, 20);
    const m4 = new THREE.Matrix4();
    let n = 0;
    for (const x of [-3.7, 3.7]) for (let z = -6; z <= 12.5; z += 2.1) {
      if (n >= 20) break;
      m4.makeTranslation(x, 4.5, z); cols.setMatrixAt(n++, m4);
    }
    cols.count = n;
    s.add(cols);

    // arches you pass through: a wall with a round-topped opening, extruded
    const wall = new THREE.Shape();
    wall.moveTo(-9, 0); wall.lineTo(9, 0); wall.lineTo(9, 11); wall.lineTo(-9, 11); wall.lineTo(-9, 0);
    const hole = new THREE.Path();
    const hw = 1.3, hh = 3.4;
    hole.moveTo(-hw, 0); hole.lineTo(-hw, hh); hole.absarc(0, hh, hw, Math.PI, 0, true); hole.lineTo(hw, 0); hole.lineTo(-hw, 0);
    wall.holes.push(hole);
    const archGeo = new THREE.ExtrudeGeometry(wall, { depth: 0.7, bevelEnabled: true, bevelSize: 0.04, bevelThickness: 0.04, bevelSegments: 2, curveSegments: 48 });
    for (const z of ARCHES) {
      const a = new THREE.Mesh(archGeo, stone);
      a.position.z = z - 0.35;
      s.add(a);
    }

    // the back wall: vertical slats, with a soft green light behind the statue
    const slat = new THREE.BoxGeometry(0.07, 11, 0.16);
    const slats = new THREE.InstancedMesh(slat, stone, 90);
    let k = 0;
    for (let x = -9; x <= 9 && k < 90; x += 0.21) { m4.makeTranslation(x, 5.5, -5.2); slats.setMatrixAt(k++, m4); }
    slats.count = k;
    s.add(slats);
    const back = new THREE.Mesh(new THREE.PlaneGeometry(20, 12), new THREE.MeshStandardMaterial({ color: 0x040706, roughness: 0.9 }));
    back.position.set(0, 6, -5.35);
    s.add(back);
    this.glowMat = new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(0x2f9b75) }, uA: { value: 0.55 } },
      vertexShader: SHEET_VERT, fragmentShader: GLOW_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(7, 7), this.glowMat);
    glow.position.set(0, 2.4, -5.0);
    s.add(glow);

    // the gantry: a thin ring around the statue (a CT scanner's bore, drawn as one line) with an orb
    this.ring = new THREE.Group();
    const ringMat = new THREE.MeshBasicMaterial({ color: MINT, transparent: true, opacity: 0.85, toneMapped: false });
    this.ringLine = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.0045, 8, 220), ringMat);
    this.orb = new THREE.Mesh(new THREE.SphereGeometry(0.035, 24, 16), new THREE.MeshBasicMaterial({ color: MINT, toneMapped: false }));
    this.ringLine.add(this.orb);
    this.ring.add(this.ringLine);
    s.add(this.ring);
  }

  #lights() {
    const s = this.scene;
    s.add(new THREE.HemisphereLight(0x9fc4b6, 0x030605, 0.55));
    // green uplights grazing the back slats, as in a gallery
    for (const x of [-2.6, 2.6]) {
      const up = new THREE.SpotLight(0x5fd6a6, 90, 12, 0.55, 0.9, 2);
      up.position.set(x, 0.15, -3.9);
      up.target.position.set(x * 1.2, 7, -5.2);
      s.add(up, up.target);
    }
    const key = new THREE.SpotLight(0xfff6ea, 260, 20, 0.42, 0.65, 2);
    key.position.set(-3.2, 7.5, 4.2);
    key.target.position.set(0, 1.7, 0);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    key.shadow.camera.near = 3; key.shadow.camera.far = 16;
    s.add(key, key.target);
    const rim = new THREE.SpotLight(0x7fe3b8, 160, 16, 0.5, 0.8, 2);
    rim.position.set(2.8, 5.5, -3.2);
    rim.target.position.set(0, 2.0, 0);
    s.add(rim, rim.target);
    const fill = new THREE.PointLight(0xbfd8cf, 6, 10, 2);
    fill.position.set(2.6, 2.2, 3.2);
    s.add(fill);
    this.keyLight = key;
  }

  /* the statue arrives: four systems as solid meshes; the muscle layer is the marble */
  setBody(body) {
    const ex = body.meta.extent_m;
    this.extent = ex;
    this.inner = new THREE.Group();
    this.inner.scale.setScalar(ex);
    this.statue.add(this.inner);
    this.statue.position.y = PEDESTAL_TOP - body.meta.bounds_m[0][1];
    this.headWorld = new THREE.Vector3(0, this.statue.position.y, 0);

    this.sys = {};
    for (const s of body.systems) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(s.pos, 3, true));
      g.setAttribute("normal", new THREE.BufferAttribute(s.nrm, 3, true));
      g.setIndex(new THREE.BufferAttribute(s.idx, 1));
      g.computeBoundingSphere();
      let mat, u;
      if (s.name === "muscle") {
        mat = new THREE.MeshStandardMaterial({ color: 0xd9ddd6, roughness: 0.38, metalness: 0.0, envMapIntensity: 1.2, emissive: 0x000000 });
        u = dissolving(mat, { veins: true, scan: true });
        u.uCut.value = 1; // hidden until it materialises
      } else {
        mat = new THREE.MeshStandardMaterial({ color: SYSTEM_COLOURS[s.name], roughness: s.name === "skeleton" ? 0.55 : 0.4, metalness: 0.0, emissive: 0x000000 });
        u = dissolving(mat, { invert: true });
      }
      const mesh = new THREE.Mesh(g, mat);
      // only the marble casts: the systems inside it never reach the floor's light
      mesh.castShadow = s.name === "muscle"; mesh.receiveShadow = s.name === "muscle";
      mesh.visible = s.name === "muscle";
      this.inner.add(mesh);
      this.sys[s.name] = { mesh, u };
    }

    // the anchors the AI beat labels, in the statue's frame (metres)
    this.anchors = Object.fromEntries(body.meta.anchors.map((a) => [a.label, { ...a, v: new THREE.Vector3(...a.at) }]));

    // the head's surface, sampled from the marble: the front-most point near (x, y), anterior -Z
    const m = body.systems.find((s) => s.name === "muscle");
    const surface = (xT, yT, tol = 0.012) => {
      let z = 0;
      for (let i = 0; i < m.pos.length; i += 3) {
        const x = m.pos[i] / 32767 * ex, y = m.pos[i + 1] / 32767 * ex;
        if (Math.abs(x - xT) < tol && Math.abs(y - yT) < tol) z = Math.min(z, m.pos[i + 2] / 32767 * ex);
      }
      return new THREE.Vector3(xT, yT, z);
    };
    // the identity the privacy beat strips, pinned where it would sit on a person (local +x faces
    // the camera in that beat)
    for (const [label, x, y] of [["id-name", 0.0, 0.03], ["id-id", 0.03, -0.045], ["id-birth", 0.035, -0.1],
      ["id-date", 0.055, 0.0], ["id-site", 0.02, -0.14]]) {
      this.anchors[label] = { label, v: surface(x, y) };
    }

    // the visor: a thin glowing band across the eyes, the reference's mark of a removed identity
    const eyeY = -0.042;
    const front = surface(0, eyeY, 0.015).z;
    const nose = Math.min(front, surface(0, -0.07, 0.015).z);
    const r = 0.082;
    this.visorMat = new THREE.MeshBasicMaterial({ color: MINT, transparent: true, opacity: 0, toneMapped: false, side: THREE.DoubleSide });
    this.visor = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 0.016, 48, 1, true, Math.PI - 1.0, 2.0), this.visorMat);
    this.visor.position.set(0, eyeY, front + r - 0.006);
    this.visorY = eyeY;
    this.statue.add(this.visor);

    // S7 on the statue: a sheet sweeps in to a plane 3.5 cm behind the nose and sears the face
    // along it (the seam is confined to the head, above the chin)
    this.faceCut = nose + 0.035;
    this.faceFrom = nose - 0.09;
    this.sys.muscle.u.uCutYMin.value = -0.13 / ex;
    this.sheetMat = new THREE.ShaderMaterial({ uniforms: { uA: { value: 0 } }, vertexShader: SHEET_VERT, fragmentShader: SHEET_FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
    this.sheet = new THREE.Mesh(new THREE.PlaneGeometry(0.24, 0.27), this.sheetMat);
    this.sheet.position.set(0, -0.03, this.faceFrom);
    this.sheet.renderOrder = 2;
    this.statue.add(this.sheet);

    this.appearAt = performance.now();
    this.ready = true;
    this.#loop();
  }

  /* HTML overlays that follow a point in the scene (labels, the tag card, the gesture caption) */
  follow(el, at, alpha) {
    const o = { el, at, alpha };
    if (el.dataset.lane) {
      o.lane = el.dataset.lane.split(",").map(Number);
      o.name = el.querySelector("span");
      o.lead = el.querySelector("b");
    }
    this.overlays.push(o);
  }

  anchorWorld(label, out) {
    const a = this.anchors && this.anchors[label];
    if (!a) return null;
    out.copy(a.v);
    out.y += this.#lift(out.y);
    return this.statue.localToWorld(out);
  }

  /* how far the scan beat's parted slices have lifted a point at height y (statue frame, metres);
     the same arithmetic as the marble's vertex shader, so labels and the sheet stay on the body */
  #lift(y) {
    const spread = this.sys ? this.sys.muscle.u.uSpread.value : 0;
    if (spread <= 0) return 0;
    const ex = this.extent;
    return Math.floor((y / ex + 1) * SLICES) * spread * 0.0032 * ex;
  }

  handWorld(out) { return this.hand ? this.hand.localToWorld(out.set(0, 5.6, 0)) : null; }

  #hand() {
    const n = HAND_OPEN.length;
    this.handPos = new Float32Array(n * 3);
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(this.handPos, 3));
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.BufferAttribute(this.handPos, 3));
    lg.setIndex(HAND_EDGES.flat());
    this.handMat = new THREE.PointsMaterial({ color: MINT, size: 0.03, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
    this.handLineMat = new THREE.LineBasicMaterial({ color: MINT, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
    this.hand = new THREE.Group();
    this.hand.add(new THREE.Points(pg, this.handMat), new THREE.LineSegments(lg, this.handLineMat));
    this.hand.scale.setScalar(0.075);
    this.scene.add(this.hand);
    // the other hand, open and mirrored: it sweeps the rest away
    const other = new THREE.BufferGeometry();
    other.setAttribute("position", new THREE.BufferAttribute(new Float32Array(HAND_OPEN.flat()), 3));
    const otherLines = other.clone();
    otherLines.setIndex(HAND_EDGES.flat());
    this.hand2Mat = this.handMat.clone();
    this.hand2LineMat = this.handLineMat.clone();
    this.hand2 = new THREE.Group();
    this.hand2.add(new THREE.Points(other, this.hand2Mat), new THREE.LineSegments(otherLines, this.hand2LineMat));
    this.hand2.scale.set(-0.075, 0.075, 0.075);
    this.scene.add(this.hand2);
  }

  #setHand(a, b, k) {
    const p = this.handPos;
    for (let i = 0; i < a.length; i++) for (let c = 0; c < 3; c++) p[i * 3 + c] = lerp(a[i][c], b[i][c], k);
    for (const o of this.hand.children) o.geometry.attributes.position.needsUpdate = true;
  }

  #bindPointer() {
    // drag to peek; the view springs back to its path on release
    const c = this.canvas;
    let down = null;
    c.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY, yaw: this.dragYaw, pitch: this.dragPitch }; this.dragging = true; c.setPointerCapture(e.pointerId); });
    c.addEventListener("pointermove", (e) => {
      if (!down) return;
      this.dragYaw = Math.max(-1.4, Math.min(1.4, down.yaw + (e.clientX - down.x) * 0.006));
      this.dragPitch = Math.max(-0.25, Math.min(0.25, down.pitch + (e.clientY - down.y) * 0.003));
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
    if (!this.running) this.render();
  }

  /* camera at progress t: smoothstep between keyframes (pairs of equal keys hold still) */
  #pose(t, pos, look) {
    let i = 0;
    while (i < KEYS.length - 2 && t > KEYS[i + 1].t) i++;
    const a = KEYS[i], b = KEYS[i + 1];
    const u = smooth((t - a.t) / Math.max(1e-6, b.t - a.t));
    pos.set(lerp(a.pos[0], b.pos[0], u), lerp(a.pos[1], b.pos[1], u), lerp(a.pos[2], b.pos[2], u));
    look.set(lerp(a.look[0], b.look[0], u), lerp(a.look[1], b.look[1], u), lerp(a.look[2], b.look[2], u));
    // narrow screens: step back so the whole figure fits
    const fit = Math.max(1, 0.9 / Math.max(0.3, this.camera.aspect));
    pos.sub(look).multiplyScalar(fit).add(look);
  }

  setProgress(t) {
    this.t = clamp01(t);
    if (!this.running) this.render();
  }

  /* everything visual, as a pure function of the (damped) progress t */
  #apply(t, time) {
    const yHead = this.headWorld ? this.headWorld.y : 2.43;

    // the gantry: tilted like an orbit in the hero, level and sweeping feet to head for the scan
    const level = span(t, 0.08, 0.12) * (1 - span(t, 0.46, 0.52));
    const sweep = span(t, 0.125, 0.215);
    const feet = PEDESTAL_TOP + 0.02, top = yHead + 0.09;
    // the hero's ring faces the camera like a halo; for the scan it lies level, a scanner's bore
    const ringY = lerp(2.05, lerp(feet, top, sweep), level);
    this.ring.position.set(0, ringY, lerp(-0.35, 0, level));
    this.ring.rotation.set(0, 0, lerp(-0.32, 0, level));
    this.ringLine.rotation.x = lerp(0.12, Math.PI / 2, level);
    const r = lerp(1.0, lerp(0.52, 0.24, span(t, 0.19, 0.23)), level);
    this.ringLine.scale.setScalar(r / 0.95);
    const orbA = time * 0.25 + t * 9;
    this.orb.position.set(Math.cos(orbA) * 0.95, Math.sin(orbA) * 0.95, 0);
    this.ringLine.material.opacity = 0.8 * (1 - span(t, 0.28, 0.33)) + 0.35 * span(t, 0.5, 0.56);
    this.orb.visible = this.ringLine.material.opacity > 0.05;

    if (!this.ready) return;
    const appear = REDUCED ? 1 : smooth((performance.now() - this.appearAt) / 700);
    const muscle = this.sys.muscle;

    // AI beat: the marble skin dissolves from the head down; the systems inside light in turn
    const peel = span(t, 0.55, 0.62) * (1 - span(t, 0.765, 0.8));
    muscle.u.uCut.value = Math.max(1 - appear, peel);
    muscle.mesh.visible = muscle.u.uCut.value < 0.995;
    const order = [["skeleton", 0.56, 0.6], ["vessels", 0.61, 0.65], ["organs", 0.65, 0.69]];
    for (const [name, a, b] of order) {
      const s = this.sys[name];
      const shown = span(t, a, b) * (1 - span(t, 0.77, 0.8));
      s.u.uCut.value = shown;
      s.u.uGlow.value = hump(t, a, b, b + 0.01, b + 0.05);
      s.mesh.visible = shown > 0.002;
    }

    // scan beat: below the gantry the marble reads as slices; the stack parts, then closes up
    // again once the identity is stripped (the scan is reassembled)
    const mu = muscle.u;
    const ex = this.extent;
    mu.uScanY.value = t < 0.12 ? -2 : (ringY - this.statue.position.y) / ex;
    mu.uScanOn.value = hump(t, 0.12, 0.13, 0.205, 0.225);
    mu.uBands.value = span(t, 0.12, 0.135) * (1 - span(t, 0.41, 0.45));
    mu.uSpread.value = 0.6 * span(t, 0.215, 0.28) * (1 - span(t, 0.4, 0.445));
    muscle.mesh.castShadow = mu.uSpread.value < 0.005; // the depth pass knows nothing of the spread

    // privacy beat: the sheet sweeps to the cut plane and sears the face; then the visor locks on
    const cutK = span(t, 0.34, 0.385);
    this.sheet.position.set(0, -0.03 + this.#lift(-0.03), lerp(this.faceFrom, this.faceCut, cutK));
    this.visor.position.y = this.visorY + this.#lift(this.visorY);
    this.sheetMat.uniforms.uA.value = hump(t, 0.335, 0.345, 0.395, 0.415);
    this.sheet.visible = this.sheetMat.uniforms.uA.value > 0.003;
    mu.uCutZ.value = this.sheet.position.z / ex;
    mu.uCutOn.value = hump(t, 0.37, 0.385, 0.42, 0.45);
    this.visorMat.opacity = 0.85 * span(t, 0.43, 0.47) * (1 - 0.6 * peel);

    // hands beat (gestures v3): a pinch-and-drag turns the statue; the hand points and holds still
    // (the glow builds, then the marble is selected); the other hand sweeps the rest away (brighter)
    const turn = span(t, 0.82, 0.86) * 0.9 * (1 - span(t, 0.95, 1.0));
    this.statueYaw = turn;
    const hold = span(t, 0.872, 0.892), clearing = span(t, 0.9, 0.935);
    muscle.u.uGlow.value = (0.12 * hold + 0.18 * (hold >= 1 ? 1 : 0) + 0.35 * clearing) * (1 - span(t, 0.95, 0.99));

    if (this.hand) {
      const on = span(t, 0.79, 0.82) * (1 - span(t, 0.95, 0.99));
      this.handMat.opacity = on;
      this.handLineMat.opacity = on * 0.9;
      this.hand.visible = on > 0.01;
      const sway = span(t, 0.82, 0.86);
      this.hand.position.set(lerp(-1.0, -0.62, sway), 1.66 + 0.04 * Math.sin(sway * Math.PI), 0.9);
      const toPinch = span(t, 0.805, 0.82), toPoint = span(t, 0.862, 0.872);
      if (toPoint <= 0) this.#setHand(HAND_OPEN, HAND_PINCH, toPinch);
      else this.#setHand(HAND_PINCH, HAND_POINT, toPoint);

      const on2 = span(t, 0.892, 0.9) * (1 - span(t, 0.95, 0.99));
      this.hand2Mat.opacity = on2;
      this.hand2LineMat.opacity = on2 * 0.9;
      this.hand2.visible = on2 > 0.01;
      this.hand2.position.set(lerp(-0.72, -1.08, clearing), 1.3, 0.9);
    }
  }

  render() {
    const time = REDUCED ? 0 : (performance.now() - this.startedAt) / 1000;
    if (this.ready && !this.hand) this.#hand();
    this.#apply(this.t, time);

    const pos = this.#pos || (this.#pos = new THREE.Vector3());
    const look = this.#look || (this.#look = new THREE.Vector3());
    this.#pose(this.t, pos, look);
    // the hero breathes (a slow drift, not a spin); the story holds still
    const drift = REDUCED ? 0 : (1 - span(this.t, 0.04, 0.1));
    pos.x += 0.25 * Math.sin(time * 0.18) * drift;
    pos.y += 0.06 * Math.sin(time * 0.23) * drift;
    this.camera.position.copy(pos);
    this.camera.lookAt(look);

    if (!this.dragging) { this.dragYaw *= 0.9; this.dragPitch *= 0.9; } // spring back
    this.statue.rotation.set(this.dragPitch, Math.PI + (this.statueYaw || 0) + this.dragYaw, 0);

    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (w && h) this.camera.setViewOffset(w, h, -this.frame[0] * w, -this.frame[1] * h, w, h);
    this.camera.updateMatrixWorld();
    this.renderer.render(this.scene, this.camera);
    this.#overlays(w, h);
    window.__journey = { t: this.t, ready: this.ready };
  }

  #pos = null;
  #look = null;
  #v = new THREE.Vector3();

  #overlays(w, h) {
    for (const o of this.overlays) {
      const a = o.alpha(this.t);
      const p = a > 0.002 ? o.at(this.#v) : null;
      if (!p) { o.el.style.opacity = "0"; o.el.style.visibility = "hidden"; o.nameW = 0; continue; }
      p.project(this.camera);
      const x = (p.x * 0.5 + 0.5) * w, y = (-p.y * 0.5 + 0.5) * h;
      o.el.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
      if (o.lane) {
        const k = Math.min(1.15, Math.max(0.6, w / 1440));
        let dx = o.lane[0] * k;
        const dy = o.lane[1] * k;
        // keep the name on screen (narrow viewports): pull it in, the leader line follows
        const nw = o.nameW || (o.nameW = o.name.offsetWidth); // measured once per appearance
        if (dx < 0) dx = Math.max(dx, 8 + nw - x);
        else dx = Math.min(dx, w - 8 - nw - x);
        o.name.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) translate(${dx < 0 ? "-100%" : "0"}, -50%)`;
        o.lead.style.width = `${Math.hypot(dx, dy).toFixed(1)}px`;
        o.lead.style.transform = `rotate(${Math.atan2(dy, dx).toFixed(4)}rad)`;
      }
      o.el.style.opacity = a.toFixed(3);
      o.el.style.visibility = p.z < 1 ? "visible" : "hidden";
    }
  }

  /* a slow GPU gets fewer pixels, not fewer frames: the pixel ratio follows the frame time */
  #slow = 0;
  #adapt(dt) {
    this.#slow = this.#slow * 0.95 + dt * 0.05;
    const r = this.renderer.getPixelRatio();
    const max = Math.min(window.devicePixelRatio || 1, 1.5);
    let next = r;
    if (this.#slow > 1 / 40 && r > 0.55) next = Math.max(0.55, r - 0.1);
    else if (this.#slow < 1 / 58 && r < max) next = Math.min(max, r + 0.05);
    if (next !== r) {
      this.renderer.setPixelRatio(next);
      this.#resize();
      this.#slow = 1 / 50; // let the new size settle before judging again
    }
  }

  #loop() {
    if (this.running || REDUCED) { if (REDUCED) this.render(); return; }
    this.running = true;
    let last = performance.now();
    const tick = (now) => {
      if (!this.visible || document.hidden) { this.running = false; return; }
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (this.onFrame) this.onFrame(dt);
      this.render();
      this.#adapt(dt);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}

export { REDUCED };

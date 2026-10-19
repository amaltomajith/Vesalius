/* Landing page behaviour: the scroll journey (one progress value drives the 3D hall, the words
   and the in-scene labels), the vow (a second pinned stage, scrubbed the same way), reveal-on-entry,
   and live proof verdicts. Server text is only ever inserted with textContent. (ES module: strict.) */

import { HallScene, CHAPTERS, loadBody, webglAvailable, REDUCED } from "./hall-scene.js";

const $ = (id) => document.getElementById(id);
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => { const t = clamp01(x); return t * t * (3 - 2 * t); };
const span = (p, a, b) => smooth((p - a) / (b - a));
const land = (p, a, b) => 1 - (1 - clamp01((p - a) / (b - a))) ** 3; // fast in, settles softly

/* ---- reveal on first entry (sections below the journey) ---- */
document.documentElement.classList.add("js");
if (REDUCED) document.documentElement.classList.add("reduced");
if ("IntersectionObserver" in window && !REDUCED) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach((n) => io.observe(n));
} else {
  document.querySelectorAll(".reveal").forEach((n) => n.classList.add("in"));
}

/* ---- the journey ---- */
const journey = $("story");
const heroLayer = document.querySelector(".hero-layer");
const chapters = new Map([...document.querySelectorAll(".chapter")].map((el) => [el.dataset.ch, el]));
const beats = [...document.querySelectorAll(".beats li")];
const beatList = document.querySelector(".beats");
const idTags = [...document.querySelectorAll(".tag3d.id")];
const gesture = $("gesture");

function journeyProgress() {
  const r = journey.getBoundingClientRect();
  const travel = r.height - window.innerHeight;
  return travel > 0 ? clamp01(-r.top / travel) : 0;
}

/** scroll so the journey shows moment t */
function seek(t) {
  const top = journey.getBoundingClientRect().top + window.scrollY;
  const travel = journey.offsetHeight - window.innerHeight;
  window.scrollTo({ top: top + travel * t, behavior: REDUCED ? "auto" : "smooth" });
}

/* words: the hero and each chapter fade and slide as pure functions of t, so they land exactly
   when their picture does and scrolling back plays them backwards */
function paintWords(t) {
  const heroOut = smooth((t - 0.03) / 0.06);
  heroLayer.style.opacity = String(1 - heroOut);
  heroLayer.style.transform = `translateY(${-32 * heroOut}px)`;
  heroLayer.style.visibility = heroOut >= 0.999 ? "hidden" : "visible";
  const story = CHAPTERS.slice(1);
  story.forEach((ch, i) => {
    const el = chapters.get(ch.key);
    const inn = smooth((t - (ch.from + 0.01)) / 0.04);
    const out = i === story.length - 1 ? smooth((t - 0.975) / 0.025) : smooth((t - (ch.to - 0.04)) / 0.035);
    const a = inn * (1 - out);
    el.style.opacity = String(a);
    el.style.transform = `translateY(${(1 - inn) * 28 - out * 28}px)`;
    el.style.visibility = a <= 0.001 ? "hidden" : "visible";
  });
  // a quiet index of the four beats, lit as they pass
  const show = span(t, 0.08, 0.12);
  beatList.style.opacity = String(show);
  beatList.style.visibility = show <= 0.001 ? "hidden" : "visible";
  beats.forEach((li) => {
    const ch = CHAPTERS.find((c) => c.key === li.dataset.beat);
    li.classList.toggle("on", t >= ch.from && t < ch.to);
    li.classList.toggle("done", t >= ch.to);
  });
  // the identity on the head: struck as the header is stripped, then gone with the face
  idTags.forEach((el) => {
    el.classList.toggle("struck", t > 0.365);
    el.classList.toggle("gone", t > 0.405);
  });
  // the gesture the hand is making
  const g = t < 0.865 ? "turn" : t < 0.896 ? "select" : "clear";
  if (gesture.dataset.on !== g) {
    gesture.dataset.on = g;
    gesture.querySelectorAll("[data-g]").forEach((s) => s.classList.toggle("on", s.dataset.g === g));
  }
}

document.querySelectorAll("a[data-seek], .nav-links a[href='#story']").forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  seek(CHAPTERS[1].from + 0.06); // "How it works": the first beat, not the hero
}));

function staticStory() {
  document.documentElement.classList.add("static-story");
}

function noWebgl() {
  $("hall-fallback").classList.remove("hidden");
  document.documentElement.classList.add("no-webgl");
  staticStory();
}

/* the labels in the scene: which system each belongs to decides when it shows */
const SYSTEM_WINDOW = { skeleton: [0.585, 0.755], vessels: [0.635, 0.755], organs: [0.675, 0.755] };

function overlays(scene) {
  for (const el of document.querySelectorAll(".tag3d:not(.id)")) {
    const [a, b] = SYSTEM_WINDOW[el.dataset.system];
    scene.follow(el, (v) => scene.anchorWorld(el.dataset.anchor, v), (t) => span(t, a, a + 0.02) * (1 - span(t, b, b + 0.02)));
  }
  idTags.forEach((el, i) => {
    const a = 0.302 + i * 0.006; // they arrive one after another
    scene.follow(el, (v) => scene.anchorWorld(el.dataset.anchor, v), (t) => span(t, a, a + 0.015) * (1 - span(t, 0.41, 0.435)));
  });
  scene.follow(gesture, (v) => scene.handWorld(v), (t) => span(t, 0.815, 0.83) * (1 - span(t, 0.94, 0.96)));
}

async function start3d() {
  if (!webglAvailable()) { noWebgl(); return; }
  if (REDUCED) staticStory(); // the chapters read as plain sections; the 3D holds the hero frame
  const scene = new HallScene($("hall-canvas"));

  // framing: the statue on the right on desktop, above the words on phones; never flips
  const narrow = window.matchMedia("(max-width: 860px)");
  const frame = () => { scene.frame = narrow.matches ? [0, -0.12] : [0.2, 0]; };
  frame();
  narrow.addEventListener("change", frame);
  overlays(scene);

  // the hall renders now; the statue streams in behind it
  loadBody().then((b) => scene.setBody(b)).catch(() => {});

  if (REDUCED) { scene.setProgress(0); return; }

  // ONE progress value, damped: a wheel notch, a scrollbar drag or a jump all glide.
  // Exponential smoothing (no overshoot) with a ~0.15 s time constant.
  let shown = journeyProgress();
  scene.setProgress(shown);
  paintWords(shown);
  scene.onFrame = (dt) => {
    // window.__hold = t pins the journey at moment t (for inspecting a beat from the console)
    const target = typeof window.__hold === "number" ? clamp01(window.__hold) : journeyProgress();
    shown += (target - shown) * (1 - Math.exp(-dt / 0.15));
    if (Math.abs(target - shown) < 1e-4) shown = target;
    scene.setProgress(shown);
    paintWords(shown);
  };
}
start3d().catch(noWebgl);

/* ---- the vow: glow floods in, the lines sharpen, the sans words soften, the tickets fly in ---- */

/** a pinned section scrubbed like the journey: IntersectionObserver runs a rAF loop only while it is
    on screen, progress is read from its box, damped (no scroll listener). window[hold] = p pins it. */
function scrub(el, paint, hold) {
  let shown = 0, running = false, last = 0;
  const progress = () => {
    const r = el.getBoundingClientRect();
    const travel = r.height - window.innerHeight;
    return travel > 0 ? clamp01(-r.top / travel) : 0;
  };
  const target = () => (typeof window[hold] === "number" ? clamp01(window[hold]) : progress());
  const tick = (now) => {
    if (!running) return;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    shown += (target() - shown) * (1 - Math.exp(-dt / 0.12));
    paint(shown);
    requestAnimationFrame(tick);
  };
  new IntersectionObserver(([e]) => {
    if (e.isIntersecting && !running) {
      running = true; shown = target(); last = performance.now(); paint(shown); requestAnimationFrame(tick);
    } else if (!e.isIntersecting) running = false;
  }).observe(el);
}

const vow = document.querySelector(".vow");
const vowStage = vow.querySelector(".vow-stage");
function paintVow(p) {
  const v = {
    glow: span(p, 0.0, 0.22), l1: span(p, 0.03, 0.15), l2: span(p, 0.09, 0.22), soft: span(p, 0.38, 0.52),
    c1: land(p, 0.45, 0.66), c2: land(p, 0.55, 0.77), c3: land(p, 0.66, 0.9), more: span(p, 0.9, 0.97),
  };
  for (const k in v) vowStage.style.setProperty(`--${k}`, v[k].toFixed(4));
}
// phones and reduced motion read the vow as a plain section (see .vow.static)
const vowNarrow = window.matchMedia("(max-width: 860px)");
const vowStatic = () => vow.classList.toggle("static", REDUCED || vowNarrow.matches);
vowStatic();
vowNarrow.addEventListener("change", vowStatic);
scrub(vow, (p) => { if (!vow.classList.contains("static")) paintVow(p); }, "__holdVow");

/* ---- proofs: latest verdicts from this machine ---- */
const VERDICT = {
  passed: ["pass", "passed"],
  failed: ["reject", "failed"],
  error: ["reject", "error"],
  incomplete: ["", "incomplete"],
};

async function paintProofs() {
  let last = {};
  try {
    // relative to the page: the API's route, or on the static site the exported copy at that path
    const r = await fetch("demo/proofs/last");
    if (r.ok) last = await r.json();
  } catch (e) { return; } // offline or server busy: the "not run yet" chips stay
  document.querySelectorAll("[data-proof]").forEach((cell) => {
    const job = last[cell.dataset.proof];
    const chip = cell.querySelector("[data-verdict]");
    if (!job || job.state !== "finished") return;
    const [cls, label] = VERDICT[job.verdict] || ["", job.verdict || "unknown"];
    chip.className = `chip ${cls}`.trim();
    chip.textContent = label;
  });
}
paintProofs();

/* Landing page behaviour: the scroll journey (one progress value drives the 3D, the words and the
   stage ribbon), reveal-on-entry below it, and live proof verdicts. Server text is only ever
   inserted with textContent. (ES module: strict.) */

import { ScanScene, CHAPTERS, loadScan, webglAvailable, REDUCED } from "/static/scan-scene.js";

const $ = (id) => document.getElementById(id);
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const smooth = (x) => { const t = clamp01(x); return t * t * (3 - 2 * t); };

/* ---- reveal on first entry (sections below the journey) ---- */
document.documentElement.classList.add("js");
if (REDUCED) document.documentElement.classList.add("reduced");
if ("IntersectionObserver" in window && !REDUCED) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  document.querySelectorAll(".reveal").forEach((n) => io.observe(n));
} else {
  document.querySelectorAll(".reveal").forEach((n) => n.classList.add("in"));
}

/* ---- the journey ---- */
const journey = $("pipeline");
const heroLayer = document.querySelector(".hero-layer");
const chapters = [...document.querySelectorAll(".chapter")];
const ribbonItems = [...document.querySelectorAll("#ribbon li")];
const fill = $("ribbon-fill");
const ribbon = document.querySelector(".ribbon");

/* each stage's moment on the timeline: its chapter's window, shared equally by its stages */
const STAGE_AT = {};
for (const ch of CHAPTERS) {
  if (!ch.stages) continue;
  ch.stages.forEach((code, i) => {
    const w = (ch.to - ch.from) / ch.stages.length;
    STAGE_AT[code] = { from: ch.from + i * w, to: ch.from + (i + 1) * w };
  });
}

function journeyProgress() {
  const r = journey.getBoundingClientRect();
  const travel = r.height - window.innerHeight;
  return travel > 0 ? clamp01(-r.top / travel) : 0;
}

/** scroll so the journey shows moment t (a stage's middle, a chapter's start) */
function seek(t) {
  const top = journey.getBoundingClientRect().top + window.scrollY;
  const travel = journey.offsetHeight - window.innerHeight;
  window.scrollTo({ top: top + travel * t, behavior: REDUCED ? "auto" : "smooth" });
}

/* words: the hero and each chapter fade and slide as pure functions of t, so they land exactly
   when their picture does and scrolling back plays them backwards */
function paintWords(t) {
  const heroOut = smooth((t - 0.035) / 0.06);
  heroLayer.style.opacity = String(1 - heroOut);
  heroLayer.style.transform = `translateY(${-28 * heroOut}px)`;
  heroLayer.style.visibility = heroOut >= 0.999 ? "hidden" : "visible";
  CHAPTERS.slice(1).forEach((ch, i) => {
    const el = chapters[i];
    const inn = smooth((t - (ch.from - 0.005)) / 0.04);
    const out = i === chapters.length - 1 ? 0 : smooth((t - (ch.to - 0.035)) / 0.04);
    const a = inn * (1 - out);
    el.style.opacity = String(a);
    el.style.transform = `translateY(${(1 - inn) * 26 - out * 26}px)`;
    el.style.visibility = a <= 0.001 ? "hidden" : "visible";
    el.classList.toggle("is-current", a > 0.5);
  });
}

/* the ribbon: a continuous fill, the current chapter's stages lit, gates that close as they pass */
function paintRibbon(t) {
  const codes = Object.keys(STAGE_AT);
  let pos = 0; // how many stages the fill covers, fractional
  codes.forEach((code, i) => {
    const s = STAGE_AT[code];
    const item = ribbonItems[i];
    const k = clamp01((t - s.from) / (s.to - s.from));
    pos += k;
    item.classList.toggle("done", t >= s.to);
    item.classList.toggle("active", t >= s.from && t < s.to);
    if (item.hasAttribute("data-gate")) item.classList.toggle("closed", t >= s.to);
  });
  fill.style.transform = `scaleX(${pos / codes.length})`;
  // the hero stays clean: the ribbon arrives with chapter 1
  const show = smooth((t - 0.06) / 0.05);
  ribbon.style.opacity = String(show);
  ribbon.style.visibility = show <= 0.001 ? "hidden" : "visible";
  const current = CHAPTERS.find((ch) => ch.stages && t >= ch.from && t < ch.to);
  ribbonItems.forEach((li, i) => li.classList.toggle("in-chapter", !!current && current.stages.includes(codes[i])));
}

ribbonItems.forEach((li) => {
  li.querySelector("button").addEventListener("click", (e) => {
    const s = STAGE_AT[e.currentTarget.dataset.stage];
    seek((s.from + s.to) / 2);
  });
});
document.querySelectorAll('a[href="#pipeline"]').forEach((a) => a.addEventListener("click", (e) => {
  e.preventDefault();
  seek(CHAPTERS[1].from + 0.02); // "How it works": the start of chapter 1, not the hero
}));

function staticStory() {
  document.documentElement.classList.add("static-story");
}

function noWebgl() {
  $("scan-fallback").classList.remove("hidden");
  document.documentElement.classList.add("no-webgl");
  staticStory();
}

async function start3d() {
  if (!webglAvailable()) { noWebgl(); return; }
  if (REDUCED) staticStory(); // the chapters read as plain sections; the 3D stays still
  const data = await loadScan();
  const scene = new ScanScene($("scan-canvas"), data);

  // framing: the model on the right half on desktop, above the words on phones; never flips
  const narrow = window.matchMedia("(max-width: 860px)");
  const frame = () => {
    scene.frame = narrow.matches ? [0, -0.16] : [0.2, 0];
    scene.bodyZoom = narrow.matches ? 1.25 : 1;
    scene.heroDim = narrow.matches ? 0.45 : 1;
  };
  frame();
  narrow.addEventListener("change", frame);

  // the atlas's levels: each chip shows or hides one body system (at least one stays on)
  const chips = [...document.querySelectorAll(".levels [data-system]")];
  chips.forEach((b) => b.addEventListener("click", () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    if (!on && chips.filter((c) => c.getAttribute("aria-pressed") === "true").length === 1) return;
    b.setAttribute("aria-pressed", String(on));
    scene.setSystems(chips.map((c) => c.getAttribute("aria-pressed") === "true"));
  }));

  if (REDUCED) { scene.setProgress(0); return; }

  // ONE progress value, damped: a wheel notch, a scrollbar drag or a jump all glide. Exponential
  // smoothing (no overshoot) with a ~0.15 s time constant.
  let shown = journeyProgress();
  scene.setProgress(shown);
  paintWords(shown);
  paintRibbon(shown);
  scene.reveal();
  scene.onFrame = (dt) => {
    const target = journeyProgress();
    shown += (target - shown) * (1 - Math.exp(-dt / 0.15));
    if (Math.abs(target - shown) < 1e-4) shown = target;
    scene.setProgress(shown);
    paintWords(shown);
    paintRibbon(shown);
  };
}
start3d().catch(noWebgl);

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
    const r = await fetch("/demo/proofs/last");
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
  // the side-by-side only exists once the face proof has passed; otherwise the empty state stays
  if (last.face && last.face.verdict === "passed") {
    const img = $("proof-img");
    img.addEventListener("load", () => { img.classList.remove("hidden"); $("proof-empty").classList.add("hidden"); });
    img.setAttribute("src", "/demo/proofs/image");
  }
}
paintProofs();

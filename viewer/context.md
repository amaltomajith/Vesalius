> **Vesalius note (phase two).** This file documents Holoscan as it was before it became the
> pipeline's viewer. Changed since: groups come from each study's `structures.json` (validated
> against mesh_contract, fail closed); the fixed atlas GLBs, the simulated vitals and the Groq
> vision call are gone (the panel is MEASUREMENTS & CONTEXT, computed from pipeline
> measurements, and no frame or study data leaves the browser); MediaPipe and fonts are vendored.
> See `refs/INTEGRATION.md` §4-5 and `refs/VIEWER-CONSTRAINTS.md` for what still holds.

﻿# Holoscan — Project Context

> **One-line summary:** A touchless, AI-powered 3D medical hologram viewer controlled entirely by hand gestures via webcam, with real-time anatomical intelligence streamed from a large language model.

---

## Table of Contents

1. [What Is Holoscan?](#what-is-holoscan)
2. [Inspiration & Origin](#inspiration--origin)
3. [Live Capabilities](#live-capabilities)
4. [Gesture Dictionary](#gesture-dictionary)
5. [Architecture Overview](#architecture-overview)
6. [Tech Stack](#tech-stack)
7. [Project Structure](#project-structure)
8. [File-by-File Breakdown](#file-by-file-breakdown)
9. [Data Flow — End-to-End](#data-flow--end-to-end)
10. [3D Model Catalogue](#3d-model-catalogue)
11. [AI Integration Details](#ai-integration-details)
12. [Design System & Aesthetic](#design-system--aesthetic)
13. [Known Quirks & Engineering Decisions](#known-quirks--engineering-decisions)
14. [What Was Planned / Roadmap Ideas](#what-was-planned--roadmap-ideas)
15. [Environment & Setup](#environment--setup)

---

## What Is Holoscan?

**Holoscan** is a browser-based **medical hologram interface** where a full human body — broken into eight anatomical systems (skeletal, cardiovascular, visceral, muscular, nervous, lymphoid, joints, body regions) — is rendered as a 3D hologram in Three.js.

The user interacts with this hologram **entirely through hand gestures** captured via the device webcam using Google MediaPipe's hand-landmarker model. No keyboard, mouse, or touchscreen required.

When a user selects an organ or bone by pointing at it and making a victory sign (✌️), a details panel slides in with structured anatomy information. They can then trigger an **AI clinical analysis** by pointing both index fingers at each other — this captures a framebuffer screenshot and streams an AI-generated medical insight from **Groq's LLaMA-4 Vision model** in real time.

The UI is styled as a **futuristic clinical HUD** — dark background, monospace fonts, gold accent colors, glassmorphic panels, and subtle animations — inspired by sci-fi surgical theatres.

---

## Inspiration & Origin

The `refs/` directory contains a reference project: **"3D Model Playground"** (by collidingScopes), a vanilla JS/Three.js demo that controlled arbitrary 3D models using MediaPipe hand gestures and voice commands. Holoscan took this as a starting point concept and rebuilt it from scratch as a:

- **React + TypeScript + Vite** single-page app
- Focused exclusively on **human anatomy**
- Added **AI-powered clinical insights** via Groq Vision API
- Replaced the vanilla JS renderer with **React Three Fiber** (R3F) + Drei
- Introduced a full **gesture state machine** with freeze-mode, swipe detection, selection, and dismiss flows
- Added a **clinical HUD** with simulated vitals, layer controls, and a gesture reference guide

---

## Live Capabilities

| Feature | How It Works |
|---|---|
| **Rotate model** | Right hand: pinch (thumb+index) + move |
| **Pan model** | Right hand: open palm + move |
| **Zoom model** | Both hands: pinch both, spread/squeeze apart |
| **Freeze/Unfreeze** | Right hand: hold fist for ~1 second |
| **Hover organ** | Left hand index finger points at model (in freeze mode) |
| **Select organ** | Left hand: victory sign ✌️ while hovering (freeze mode) |
| **Dismiss panel** | Left hand: fist clench |
| **AI snapshot** | Both index fingers point at each other simultaneously |
| **Isolate organ** | Right hand swipe right (after selection) |
| **Restore anatomy** | Click "Restore Anatomy" button in the HUD |
| **Toggle body systems** | Click layer panel buttons (left side of screen) |

---

## Gesture Dictionary

The gesture engine (`useGestureControl.ts`) handles all 21 MediaPipe hand landmarks per hand to derive semantic gestures.

### Key Landmark Indices

| Landmark | Index |
|---|---|
| Wrist | 0 |
| Thumb tip | 4 |
| Index tip | 8 |
| Middle tip | 12 |
| Ring tip | 16 |
| Pinky tip | 20 |

### Gesture State Machine

```
               No hands visible → activeGesture = NONE
                     │
           hand(s) appear in webcam frame
                     │
   Both hands pinching   →   ZOOM  (highest priority)
   Single hand pinch     →   ROTATE
   Open palm             →   PAN
   Right fist hold ~22fr →   FREEZE  (toggle)

   While FROZEN:
     left index points → hover highlight on model
     left victory ✌️  → select & open info panel
     left fist         → dismiss info panel
     wrist swipe L/R/D → swipe gestures
     both index ↔ each other → AI snapshot
```

### Gesture Priorities (in order)

1. **Two-hand zoom** — fires if both hands pinch simultaneously
2. **Freeze / Unfreeze** — right-hand fist held for 22 frames (~360 ms at 60fps)
3. **Rotate** — right hand pinch + move
4. **Pan** — right hand open palm + move
5. **Idle** — no gesture recognized

### Smoothing

Low-pass exponential filter on every landmark before classification:

```
SMOOTH = 0.55
smooth[i] = 0.55 * prev[i] + 0.45 * raw[i]
```

---

## Architecture Overview

```
Browser
  ├── HologramCanvas (React Three Fiber / WebGL)
  │     └── AnatomicalModel (useFrame loop)
  │           └── SystemLayer × 8 (GLB loaders, holographic materials)
  │
  ├── HandTracker (invisible video + overlay canvas)
  │     └── MediaPipe tasks-vision (GPU delegate, 60fps RAF)
  │
  ├── HUDOverlay (2D clinical overlay)
  │     ├── Status pill (gesture state + fist progress)
  │     ├── Vitals & System panels
  │     ├── LayerPanel (8 toggle buttons)
  │     └── Hover briefing card + gesture guide
  │
  ├── PartInfoPanel (slide-in drawer, right side)
  │     ├── Anatomy DB data
  │     └── AI Insight streaming box
  │
  ├── useGestureControl (hook, outside 3D context)
  │     └── GestureState → consumed by all components
  │
  ├── layerStore (Zustand, global)
  │     └── 8 boolean layer visibility flags
  │
  └── groqVision (async generator)
        └── Groq API SSE stream → aiInsight chunks
```

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Framework | React | 19 |
| Build Tool | Vite | 8 |
| Language | TypeScript | 6 |
| 3D Renderer | Three.js | 0.184 |
| React-3D Bridge | React Three Fiber | 9 |
| R3F Helpers | @react-three/drei | 10 |
| Hand Tracking | MediaPipe tasks-vision | 0.10.14 |
| State Management | Zustand | 5 |
| AI Vision | Groq API (LLaMA-4 Scout 17B) | — |
| Icons | Lucide React | 1.17 |
| Fonts | Outfit + JetBrains Mono (Google Fonts) | — |
| Styling | Vanilla CSS (custom properties) | — |

---

## Project Structure

```
Holoscan/
├── index.html                    # HTML entry (mounts <div id="root">)
├── vite.config.ts                # Vite config
├── package.json                  # Dependencies
│
├── public/
│   ├── favicon.svg
│   └── models/                   # GLB files served statically (~163 MB total)
│       ├── SkeletalSystem100.glb        (27 MB)
│       ├── CardioVascular41.glb         (33 MB)
│       ├── VisceralSystem100.glb        (14 MB)
│       ├── MuscularSystem100.glb        (25 MB)
│       ├── NervousSystem100.glb         (42 MB)
│       ├── LymphoidOrgans100.glb         (1.5 MB)
│       ├── Joints100.glb                 (7 MB)
│       └── Regions of human body100.glb  (4 MB)
│
├── src/
│   ├── main.tsx                  # ReactDOM.createRoot entry
│   ├── App.tsx                   # Root component — orchestrates all children
│   ├── index.css                 # Full design system (770 lines of CSS)
│   │
│   ├── components/
│   │   ├── HologramCanvas.tsx    # R3F Canvas, lights, OrbitControls, grid
│   │   ├── AnatomicalModel.tsx   # useFrame loop, raycasting, mesh animation
│   │   ├── SystemLayer.tsx       # Loads one GLB + applies holographic material
│   │   ├── HandTracker.tsx       # MediaPipe init, RAF loop, skeleton draw
│   │   ├── HUDOverlay.tsx        # Full clinical HUD
│   │   ├── LayerPanel.tsx        # System toggle buttons
│   │   └── PartInfoPanel.tsx     # Slide-in detail panel + AI insight box
│   │
│   ├── hooks/
│   │   └── useGestureControl.ts  # Full gesture state machine (651 lines)
│   │
│   ├── data/
│   │   └── anatomyDatabase.ts    # Static anatomy data for 10 structures
│   │
│   ├── services/
│   │   └── groqVision.ts         # Async generator: Groq streaming vision call
│   │
│   ├── store/
│   │   └── layerStore.ts         # Zustand store: 8 systems + toggle/setLayer
│   │
│   └── utils/
│       └── raycastUtils.ts       # Converts finger NDC to Three.js Raycaster
│
└── refs/                         # Reference/inspiration project (NOT part of app)
    ├── game.js                   # Original vanilla JS hand-gesture playground
    └── SpeechManager.js          # Voice command handler (reference only)
```

---

## File-by-File Breakdown

### App.tsx — Root Orchestrator

**State it owns:**
- `selectedMesh: string | null` — mesh name of the currently selected organ
- `hoveredMesh: string | null` — mesh name under the left index fingertip
- `partsRemoved: boolean` — whether non-selected meshes are hidden/slid away
- `aiInsight`, `aiLoading`, `aiError` — streaming AI analysis state

**Key behaviors:**
- When `selectedMesh` changes → resets AI state, cancels in-flight streams via `activeRequestRef`
- When freeze mode exits (`isFrozen` → false) → clears selection and `partsRemoved`
- When `gestureState.rightSwipeGesture` fires while a mesh is selected → triggers `partsRemoved = true`
- `handleAiSnapshot` — captures the framebuffer and launches the Groq streaming call

**Race condition guard:** `activeRequestRef` is an integer that increments on every selection change. Each stream iteration checks if its request ID is still current before setting state.

---

### HologramCanvas.tsx — 3D Scene

**Scene setup:**
- Background: near-black `#0c0b0a`
- Camera: position [0, 0, 4.5], FOV 50, near 0.005, far 100
- `preserveDrawingBuffer: true` — required so `gl.domElement.toDataURL()` works for AI snapshots
- **4-light rig:** ambient (0.15), key directional warm-white (1.1), fill cool-blue (0.45), rim warm-orange backlight (0.25), under-fill point light (0.35)
- **OrbitControls:** zoom and pan always on; orbit-rotate blocked only during live hand gesture (NOT during freeze mode)
- **Grid floor** at y = -1.2 with warm dark colors, fading at 6 units

---

### AnatomicalModel.tsx — The Brain

The most complex component. Runs inside the R3F render loop (useFrame). Handles all 3D interaction.

**Model Transforms (every frame):**
- Lerps position, rotation X/Y, and scale toward gesture-driven targets
- Smoothing factors: rotation 0.12, position 0.22, scale 0.18

**Raycasting (Freeze mode only):**
- Converts left fingertip NDC via `getFingerRay()` → raycaster
- Sets `hoveredMeshRef` → propagates `setHoveredMesh(name)` upward
- Uses mesh `uuid` internally to avoid name comparison issues

**Selection (edge-triggered):**
- **Victory ✌️ onset** (first frame going true) → `setSelectedMesh(hoveredMesh.name)`
- **Left fist onset** → `setSelectedMesh(null)`, with 500 ms dismiss cooldown

**Mesh Animations (every frame):**

When a mesh is selected:
- **Selected mesh:** opacity → 1.0, pulsing emissive glow (sin wave, 1.4–2.2 intensity)
- **All other meshes:** opacity → 0.15
- If `partsRemoved`: non-selected meshes slide out (x += 4.0 units) and fade to 0 opacity

When no mesh selected:
- All restore to full opacity
- Hovered mesh: scale → 1.05×, pulsing glow

**Auto-centering:**
Each SystemLayer reports its bounding box → `recomputeBounds()` unions all boxes and sets `autoScale` = 2.0 / modelHeight.

**AI Snapshot Gesture:**
- If both index fingertip positions are within 0.15 horizontal + 0.08 vertical → captures WebGL framebuffer and calls `onAiSnapshot(base64)`

---

### SystemLayer.tsx — GLB Loader

Loads one `.glb` file and applies holographic material to every mesh.

**Key design decisions:**
- `scene.clone(true)` in a stable ref — prevents remount flickering on visibility toggle
- Each mesh gets its **own** `MeshStandardMaterial` instance — prevents cross-mesh flicker
- Visibility toggled by `child.visible = boolean`, never by unmounting — keeps GPU buffers warm
- Material params: `emissiveIntensity: 0.3`, `transparent: true`, `opacity: 0.75`, `depthWrite: false`
- Materials disposed on unmount to prevent GPU leaks

All 8 GLBs are preloaded in parallel at startup:
```ts
SYSTEMS.forEach((s) => useGLTF.preload(s.file));
```

---

### HandTracker.tsx — MediaPipe Vision

**Initialization flow:**
1. `FilesetResolver.forVisionTasks()` — downloads WASM from jsDelivr CDN
2. `HandLandmarker.createFromOptions()` — loads model from Google Storage (GPU delegate, numHands: 2, VIDEO mode)
3. `getUserMedia({ video: { facingMode: 'user', 1280×720 } })`
4. RAF loop: `handLandmarker.detectForVideo(video, performance.now())`

**Skeleton rendering:**
- Left hand = cyan `#00f0ff`, right hand = purple `#a78bfa`
- Fingertips: large ring + colored dot with glow
- Joints: small dots with blur shadow

**Output:** Calls `onHandsUpdate(landmarks[][], handedness[])` every frame.

---

### useGestureControl.ts — Gesture Engine

651-line hook. Takes raw landmark arrays and produces a stable `GestureState` object.

**Gesture detectors:**

| Function | Logic |
|---|---|
| `countExtended(lms)` | tip.y < pip.y AND tip.y < mcp.y per finger |
| `getPinchDist(lms)` | Euclidean distance: thumb tip [4] ↔ index tip [8] |
| `isOpenPalm(lms)` | !pinching && extended >= 3 |
| `getClenchRatio(lms)` | avg tip distance to palm center / palm length |
| `isFist(lms)` | extended == 0 AND clenchRatio < 0.90 |
| `isVictory(lms)` | index + middle extended, ring + pinky curled |

**Pinch hysteresis:**
- Activates at distance < 0.05 (PINCH_ON)
- Deactivates at distance > 0.075 (PINCH_OFF)

**Tuning constants:**
```
FIST_FRAMES     = 22      // frames to hold fist (~360ms @ 60fps)
FREEZE_COOLDOWN = 2000    // ms between freeze toggles
ROT_SENS_Y      = 3.2     // yaw sensitivity
ROT_SENS_X      = 2.2     // pitch sensitivity
PAN_SENS        = 5.0     // pan sensitivity
ZOOM_SENS       = 3.5     // exponential zoom sensitivity
SCALE_MIN       = 0.08
SCALE_MAX       = 60.0
```

**Swipe detection:** Rolling 10-frame wrist history. Fires when 5+ consecutive frames show directional movement > 0.04 units/frame. Only active in freeze mode.

**Zoom math:** Exponential: `nextScale = baseScale * exp(pinchDelta * ZOOM_SENS)`, pivoting around midpoint of the two pinch points.

---

### HUDOverlay.tsx — Clinical HUD

**Layout (CSS Grid):**
- `grid-template-rows: auto 1fr auto` → top bar, content, bottom bar
- Pointer events disabled on container; enabled individually via `hud-interactive` class

**Panels:**
- **Top bar:** HOLOSCAN logo, center status pill (gesture state + fist progress bar), LIVE badge + clock
- **Vitals panel:** heart rate 64–82 bpm (simulated), body temp 36.4–37.2°C (simulated), ECG "Stable"
- **System panel:** FPS 58–62 (simulated), engine: Three.js, tracking status
- **Layer slot:** LayerPanel at vertical center-left (absolute positioned)
- **Hover briefing card:** Appears at bottom center in freeze mode when mesh is hovered — shows organ name, system, ✌️ instruction
- **Bottom bar:** "Restore Anatomy" button (conditional), "How to Use" gesture guide toggle

---

### PartInfoPanel.tsx — Detail Drawer

Slide-in panel from the right when an organ is selected.

**Sections:**
1. Header: organ display name + system badge, close button
2. Physiological Function
3. Anatomical Location
4. Clinical Significance (highlighted in gold)
5. **MED-AI INSIGHT** box — streaming Groq analysis

**States:** AWAITING SNAPSHOT → ANALYZING... → ANALYSIS COMPLETE (or error)

**Slide animation:** CSS `transform: translateX(120%)` → `translateX(0)` with spring curve.

**Gesture dismiss:** Watches `leftFistActive` prop; calls `onClose()` when fist is detected.

---

### layerStore.ts — Global State

Zustand store for anatomy layer visibility.

**8 systems:**

| Key | Label | Color | Default |
|---|---|---|---|
| `skeletal` | Skeletal | Silver-blue `#C0C8D0` | ON |
| `cardiovascular` | Cardiovascular | Hot pink `#FF4466` | ON |
| `visceral` | Visceral | Orange `#FF8844` | ON |
| `muscular` | Muscular | Pink-red `#FF6688` | OFF |
| `nervous` | Nervous | Electric yellow `#FFEE44` | OFF |
| `lymphoid` | Lymphoid | Cyan-teal `#44FFEE` | OFF |
| `joints` | Joints | Electric blue `#4488FF` | OFF |
| `regions` | Regions | Slate `#8090A0` | OFF |

---

### anatomyDatabase.ts — Knowledge Base

Static lookup table mapping GLB mesh names to structured anatomy data.

**Currently mapped (10 structures):**

| Mesh Name | Display Name | System |
|---|---|---|
| `Kidneyl` | Left Kidney | Urinary |
| `Kidneyr` | Right Kidney | Urinary |
| `Liver001` | Liver | Digestive |
| `Stomach001` | Stomach | Digestive |
| `Epiglottis` | Epiglottis | Respiratory |
| `Trachea` | Trachea | Respiratory |
| `Urinary_bladder` | Urinary Bladder | Urinary |
| `Frontal_bone001` | Frontal Bone | Skeletal |
| `Pleura` | Pleura | Respiratory |
| `Pancreas` | Pancreas | Digestive |

**Fallback logic:** Strips trailing numbers, then underscores → spaces → title case. Every mesh gets displayed data.

Each entry has: `displayName`, `system`, `function`, `location`, `clinicalNote`.

---

### groqVision.ts — AI Service

Streaming chat completion to Groq's OpenAI-compatible API.

- **Model:** `meta-llama/llama-4-scout-17b-16e-instruct`
- **Input:** base64 PNG (WebGL framebuffer) + selected mesh name
- **Output:** async generator yielding text chunks (SSE stream)
- **Prompt:** asks the model to respond as a medical AI with: what's visible in the 3D view, clinical significance, surgical notes — under 100 words
- **Temperature:** 0.1 (very deterministic)
- **Max tokens:** 150
- **API key:** `VITE_GROQ_API_KEY` env variable

---

### raycastUtils.ts — 3D Picking

Converts MediaPipe's normalized 0–1 screen coordinates to Three.js NDC:

```
ndcX = fingertipX * 2 - 1
ndcY = -fingertipY * 2 + 1    // invert Y
```

Returns a configured `THREE.Raycaster` for `intersectObject()`.

---

### index.css — Design System

770-line CSS defining every visual token and component style.

**Design tokens:**

```css
--bg:             #0c0b0a          /* Near-black background */
--gold:           #c9a96e          /* Primary accent color */
--text-hi:        #f2ede8          /* High-contrast text */
--text-mid:       #8a8480          /* Default body text */
--text-lo:        #4a4640          /* Muted/label text */
--font-ui:        'Outfit'         /* Display font */
--font-mono:      'JetBrains Mono' /* Data/code font */
--green:          #4ade80          /* Status: active/OK */
--red:            #f87171          /* Status: frozen/error */
```

**Key animations:** breathe, subtle-pulse, spin-ring, slide-up-fade, scanline-glow, hand-pinch-pulse

**Z-index layers:**
```
 5 — 3D canvas
 8 — hand skeleton overlay
10 — HUD container
20 — layer panel slot
25 — part info panel
30 — hover briefing card
40 — gesture guide popover
```

---

## Data Flow — End-to-End

```
Webcam
  │
  ▼
HandTracker (RAF loop, 60fps)
  │  NormalizedLandmark[][] + handedness[]
  ▼
useGestureControl.updateHands()
  │  → landmark smoothing
  │  → gesture classification
  │  → GestureState update (React state)
  ▼
App.tsx (re-render)
  │
  ├──► HologramCanvas → AnatomicalModel (useFrame)
  │       ├── lerps model transforms
  │       ├── raycasts left finger in freeze mode
  │       ├── detects victory/fist onset edges
  │       ├── animates mesh opacity/glow/position
  │       └── detects AI snapshot gesture
  │              └──► onAiSnapshot(base64)
  │                      └──► groqVision.streamGroqVision()
  │                              └──► chunks → aiInsight state
  │
  ├──► HUDOverlay → renders status, vitals, layer toggles, hover briefing
  │
  └──► PartInfoPanel → anatomy data + streaming AI text

HandTracker overlay canvas
  └── draws colored hand skeleton (independent of React)
```

---

## 3D Model Catalogue

All GLB files in `public/models/`. Total ~163 MB.

| File | Size | Notes |
|---|---|---|
| `SkeletalSystem100.glb` | 27 MB | Default ON |
| `CardioVascular41.glb` | 33 MB | Default ON |
| `VisceralSystem100.glb` | 14 MB | Default ON |
| `MuscularSystem100.glb` | 25 MB | Default OFF |
| `NervousSystem100.glb` | 42 MB | Default OFF — largest file |
| `LymphoidOrgans100.glb` | 1.5 MB | Default OFF |
| `Joints100.glb` | 7 MB | Default OFF |
| `Regions of human body100.glb` | 4 MB | Default OFF |

All 8 preloaded in parallel at startup. First 3 (skeletal, cardiovascular, visceral) appear immediately; others stream in the background and appear when toggled.

---

## AI Integration Details

### Trigger Mechanism

Both index fingers pointed at each other (dx < 0.15, dy < 0.08 in normalized screen space) → captures WebGL framebuffer as PNG via `gl.domElement.toDataURL()`. This is why `preserveDrawingBuffer: true` is set.

### API Details

```
POST https://api.groq.com/openai/v1/chat/completions
Model: meta-llama/llama-4-scout-17b-16e-instruct
Stream: true | Temperature: 0.1 | Max tokens: 150
```

### Prompt Strategy

Names the selected mesh, asks for: what is visible in the 3D view, key clinical significance, what a surgeon should note. Constrained to under 100 words.

### Stream Handling

Text chunks stream token-by-token into `aiInsight` state. Race conditions handled by `activeRequestRef` — if user selects a different organ mid-stream, old chunks are silently discarded.

### Environment Variable

Requires `VITE_GROQ_API_KEY` in `.env`. Get a free key at https://console.groq.com

---

## Design System & Aesthetic

Themed as a **near-future surgical hologram table** ("HOLOTABLE_01").

**Typography:**
- **Outfit** — display/UI text (weights 300–900)
- **JetBrains Mono** — all data, labels, codes

**Color philosophy:**
- Dark warm-black base (slightly warm, not pure black)
- Gold `#c9a96e` as the singular accent — premium, clinical, cautious
- Green = active/safe, Red = frozen/alert

**Glassmorphism:** Panels use `backdrop-filter: blur(12–20px)` with translucent backgrounds.

**Animation philosophy:** Everything moves — logo dot breathes, status dot pulses, loading spinner spins, panel entries spring, ✌️ icon pulses, scanline glows. Nothing is ever truly static.

---

## Known Quirks & Engineering Decisions

1. **`preserveDrawingBuffer: true`** — Required for `toDataURL()`, slight GPU performance cost.

2. **No OrbitControls block in freeze mode** — Deliberate. Allows scroll-zoom/pan to inspect an organ before selecting it. Only live hand gestures block orbit-rotate.

3. **Mesh UUID vs name** — Internal animations use `mesh.uuid` (stable, unique). External anatomy DB uses `mesh.name` (human-readable). Both are needed.

4. **Edge-triggering gestures** — Victory and fist use `prevVictoryRef`/`prevFistRef` to fire only on the _onset frame_, preventing rapid repeat-firing while gesture is held.

5. **`scene.clone(true)` in SystemLayer** — Without cloning, modifying one system's material would affect all references to the same scene object.

6. **Vite `refs/` exclusion** — `rollupOptions.input: 'index.html'` ensures Vite doesn't try to bundle the reference project in `refs/`.

7. **MediaPipe CDN loading** — WASM runtime (~2 MB) and hand model (~24 MB) are loaded from CDN at runtime, not bundled. Requires internet access on first use; browser-cached thereafter.

8. **`depthWrite: false` on all materials** — Required for correct transparent rendering of overlapping anatomical layers. Without it, one organ clips through another.

9. **Anatomy database gaps** — Only 10 of hundreds of GLB meshes are explicitly mapped. The fallback auto-generates display names but provides no real clinical data.

---

## What Was Planned / Roadmap Ideas

### Near-term (partially scaffolded)
- **More anatomy entries** — `anatomyDatabase.ts` has only 10 entries for potentially hundreds of selectable meshes. Systematic coverage needed.
- **Voice commands** — The `refs/` directory has a full `SpeechManager.js` implementation. A voice interface ("show nervous system", "zoom in", "select liver") would be a natural next step.

### Medium-term (architectural hooks exist)
- **Per-organ camera focus** — When an organ is selected, the camera could auto-zoom to that organ. OrbitControls `makeDefault` ref is ready for this.
- **Swipe gesture actions** — The swipe detection infrastructure exists for LEFT, RIGHT, and DOWN swipes. Currently only right swipe triggers "remove parts". LEFT and DOWN swipes are detected but have no action assigned yet.
  - LEFT swipe → cycle visible systems?
  - DOWN swipe → reset model position/rotation/scale?

### Longer-term
- **Patient data integration** — Vitals panel shows simulated data (`PATIENT_081 · THORAX_3D`). Real integration with FHIR API or patient records.
- **AR/XR mode** — R3F has WebXR support. WebXR hand input could replace MediaPipe, making this a true AR hologram viewer.
- **Surgical annotation** — Mark organs, add notes, save as session data.
- **Multi-user collaboration** — WebSockets to share gesture state across multiple screens (teaching/consultation).
- **Different body models** — Gender-specific, pediatric, or pathological models (e.g., with tumors/disease markers).
- **Voice narration** — TTS reading AI insights aloud for hands-free operation during surgery or teaching.
- **Anatomy quiz mode** — Freeze model, hide labels, ask user to identify structures.
- **Export/report** — Generate PDF of selected parts + AI insights for documentation.

### Code-level TODOs
- Debug `console.log` statements in `AnatomicalModel.tsx` (lines 214–215) left in production code
- No `.env.example` file — contributors need to know about `VITE_GROQ_API_KEY`
- `LayerPanel` has no keyboard navigation (accessibility gap)
- Anatomy database needs systematic expansion to cover all mesh names in the GLBs

---

## Environment & Setup

### Prerequisites
- Node.js 18+
- A Groq API key (free tier at https://console.groq.com)
- A webcam
- Modern browser with WebGL + WebAssembly (Chrome recommended)

### Setup

```bash
# Install dependencies
npm install

# Create environment file
echo "VITE_GROQ_API_KEY=your_key_here" > .env

# Start dev server
npm run dev
```

### Build

```bash
npm run build    # TypeScript compile + Vite bundle → dist/
npm run preview  # Preview production build locally
```

### First Run Notes
- On first load, MediaPipe WASM (~2 MB) + hand model (~24 MB) download from CDN
- All 8 GLB models start downloading in parallel (~163 MB total)
- Browser requests webcam permission on load
- First 3 systems (skeletal, cardiovascular, visceral) appear as soon as their GLBs parse

---

## Summary

Holoscan is a technically ambitious **gesture-driven 3D medical education and visualization tool**. It combines:

- Real-time **computer vision** (MediaPipe, 60fps)
- Sophisticated **gesture state machines** (freeze, rotate, pan, zoom, select, dismiss, swipe, snapshot)
- **Multi-system 3D rendering** in Three.js/R3F with per-mesh material control and frame-accurate animation
- **Streaming AI vision analysis** (Groq LLaMA-4)
- A **premium clinical UI** that looks and feels like science fiction

The core experience is fully touchless — the user only needs their hands and a webcam to navigate an entire human body in 3D, select organs, isolate them, and receive AI-generated clinical insights in real time.

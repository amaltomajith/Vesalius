# Viewer Constraints — Holoscan

Load-bearing invariants in the existing codebase. **Several of these look like cruft and are
not.** Removing them causes rendering bugs that are hard to trace back.

This file covers *what must not break*. `INTEGRATION.md` covers what to change.

---

## Non-negotiable rendering settings

| Setting | Where | Why it exists |
|---|---|---|
| `depthWrite: false` | every mesh material | Overlapping transparent anatomical layers render incorrectly without it — organs clip through each other. Not optional, not a style choice |
| `preserveDrawingBuffer: true` | R3F `Canvas` gl props | `gl.domElement.toDataURL()` returns blank without it. Costs some GPU performance. Only needed if framebuffer capture is retained — see the AI regrounding note below |
| `scene.clone(true)` | `SystemLayer` | GLTF scenes are shared references. Without cloning, changing one system's material mutates every other reference to the same loaded asset |
| Per-mesh `MeshStandardMaterial` | `SystemLayer` | Each mesh gets its own material instance. Sharing causes cross-mesh opacity and glow flicker during selection |
| `child.visible = bool` | layer toggling | Visibility toggles never unmount. Unmounting drops GPU buffers and causes a visible reload stall on every toggle |
| Material disposal on unmount | `SystemLayer` | Prevents GPU memory leaks across study switches. **More important now** — the viewer previously loaded eight models once; it will now load a new set per study |

---

## Raycasting: uuid vs name

Both are needed and they are not interchangeable.

- **`mesh.uuid`** — used internally for per-frame animation state (which mesh is selected,
  which is hovered). Stable and unique. Never compare by name here
- **`mesh.name`** — used for anatomy lookup and now for the segmentation-label contract.
  Human-readable, not guaranteed unique across a scene

Keep the split. Collapsing them will produce animation applied to the wrong mesh when two
nodes share a name.

NDC conversion from MediaPipe normalized coordinates:

```
ndcX =  fingertipX * 2 - 1
ndcY = -fingertipY * 2 + 1     // Y inverted
```

---

## Gesture engine — do not retune

`useGestureControl.ts` constants are empirically tuned against real hand tracking. Changing
them without a webcam in front of you will degrade usability in ways unit tests cannot catch.

```
SMOOTH          = 0.55     low-pass filter on landmarks
PINCH_ON        = 0.05     hysteresis: activate below
PINCH_OFF       = 0.075    hysteresis: deactivate above
FIST_FRAMES     = 22       ~360 ms hold to toggle freeze
FREEZE_COOLDOWN = 2000     ms between freeze toggles
ROT_SENS_Y      = 3.2
ROT_SENS_X      = 2.2
PAN_SENS        = 5.0
ZOOM_SENS       = 3.5      exponential
SCALE_MIN       = 0.08
SCALE_MAX       = 60.0
```

**Pinch hysteresis** (separate on/off thresholds) prevents flicker at the boundary. A single
threshold will oscillate.

**Edge-triggering** — victory-select and fist-dismiss fire on the onset frame only, guarded by
`prevVictoryRef` / `prevFistRef`. Without this they repeat-fire every frame the gesture is
held.

**Gesture priority order is fixed:** two-hand zoom → freeze toggle → rotate → pan → idle.
Reordering breaks the interaction model.

**Interpolation factors** in `AnatomicalModel`: rotation 0.12, position 0.22, scale 0.18.
These make gesture input feel responsive without jitter.

---

## Component responsibilities

| File | Owns |
|---|---|
| `App.tsx` | `selectedMesh`, `hoveredMesh`, `partsRemoved`, AI state. Cancels in-flight streams via `activeRequestRef` on selection change |
| `HologramCanvas.tsx` | R3F canvas, 4-light rig, OrbitControls, grid |
| `AnatomicalModel.tsx` | Per-frame loop: transform lerping, raycasting, selection edges, mesh animation |
| `SystemLayer.tsx` | Loads one GLB, applies holographic material per mesh, reports bounding box |
| `HandTracker.tsx` | MediaPipe init, RAF detection loop, skeleton overlay |
| `useGestureControl.ts` | Landmark smoothing → semantic `GestureState` |
| `layerStore.ts` | Layer visibility (**being replaced with dynamic groups**) |
| `anatomyDatabase.ts` | Structure metadata (**being replaced with manifest-first lookup**) |

**`activeRequestRef` race guard:** an integer incremented on every selection change. Each
stream iteration checks its ID is still current before setting state. Preserve this when
regrounding the AI call — the race it prevents is real.

**Auto-centring:** each `SystemLayer` reports its bounding box; `recomputeBounds()` unions them
and sets `autoScale = 2.0 / modelHeight`. With pipeline GLBs sharing an origin this still
works, but verify — patient anatomy varies in extent far more than a fixed atlas model.

---

## Loading behaviour that changes

The current viewer preloads eight static GLBs in parallel at startup — 163 MB, cached
thereafter.

Pipeline studies are **per-patient and not cacheable across studies**. Consequences:

- Preloading every group on study select is wrong. Load default-visible groups first, fetch
  others on toggle
- A loading state per group is now necessary. The current UI assumes models are simply there
- Dispose the previous study's materials and geometries on switch. This was never exercised
  when eight models loaded once
- Respect the payload budget in `INTEGRATION.md` §2. The viewer cannot absorb 163 MB per patient

---

## Z-index layers

```
 5  3D canvas
 8  hand skeleton overlay
10  HUD container
20  layer panel slot
25  part info panel
30  hover briefing card
40  gesture guide popover
```

New UI (study picker, provenance line, loading states) must slot into this scheme. The HUD
container disables pointer events and re-enables them per element via `hud-interactive` —
new interactive elements need that class or they will not receive clicks.

---

## Design tokens

**Changed in phase three (user decision, 2026-09-27: "surgical green everywhere").** The viewer is
now the Phantom app and shares the landing page's palette. Source: `viewer/src/styles/tokens.css`.

```css
/* the scan room (hologram + full body) */
--room-bg:    #0a2a21    /* deep surgical green */
--room-mint:  #c9f2df    /* single accent (was gold) */
--room-ink:   #f3f5f1
--room-ink-2: rgb(243 245 241 / 0.7)
--room-ink-3: rgb(201 242 223 / 0.45)
--room-coral: #ff7a66    /* frozen / refused / error only */
/* the dashboard screens: bone-white #f3f5f1, accent #0f6b52, deep #0f3b2f */
--sans: 'Geist'   --mono: 'Geist Mono'   (bundled, no font request)
```

Mint is the **only** accent in the scan room. Group colours are the exception and belong to the
layer system, not the UI chrome. The HUD's old token names (`--gold`, `--text-hi`, ...) are kept in
`index.css` and remapped to these values inside `.room`, so its rules are unchanged. Icons are
Phosphor (no emoji). Everything else in this document (rendering settings, gestures, z-index,
raycasting) is unchanged.

---

## Stack

React 19 · Vite 8 · TypeScript 6 · Three.js 0.184 · React Three Fiber 9 · Drei 10 ·
MediaPipe tasks-vision 0.10.14 · Zustand 5 · React Router 7 · Phosphor icons · vanilla CSS custom
properties.

MediaPipe WASM (~2 MB) and the hand model (~24 MB) load from CDN at runtime, not bundled.
First run requires network.

---

## Out of scope

The existing project documentation contains a roadmap and a TODO list. **None of it is in
scope for this integration.** Specifically do not implement: voice commands, AR/XR mode,
per-organ camera focus, LEFT/DOWN swipe actions, multi-user collaboration, quiz mode, PDF
export, or TTS narration.

If a roadmap item seems necessary to complete an integration task, say so and stop rather
than building it.

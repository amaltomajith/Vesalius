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

## Gesture engine (v3.3, 2026-10-18)

**Rewritten at the user's request**, twice. v1 was too hard to use (select needed freeze + a
left-hand victory sign; isolate needed a right-hand swipe that almost never registered). v2's
navigation was kept, but its quick-pinch air-click was still hard to time, so v3 selects by
pointing and holding, and clears the rest with a sweep of the other hand. Both engines are pure
and unit-tested: `hooks/gestureEngine.ts` (poses, navigation, the second hand) and
`hooks/selectionEngine.ts` (hold to select, sweep to clear); `useGestureControl.ts` only wraps
them for React.

**The model.** The primary hand (the right if visible, else the left) steers and selects. The
other hand zooms (both pinching) and, once a part is selected, sweeps. There is no freeze mode.

```
open palm             rest: nothing moves
pinch and drag        turn the model
fist and drag         move the model
both hands pinch      zoom
point                 cursor at the index tip; hovering a part highlights it and names it
point and hold 2 s    on a part: select it; on empty space: step back
other hand: open palm, held still 0.4 s (armed, its marker lights), then swept
                      (a part selected) the rest follows the hand, then flies off that way: isolate
                      (isolated) the rest flies back in following the hand: show everything
both index fingers pointing at each other   AI snapshot (atlas only, a part selected)
```

**v3.1: a sweep has to be meant** (user report: zooming into an isolated part brought the body
back; the other hand's pinch was the only thing telling zoom from sweep, so a loosening pinch, an
open hand on its way into a zoom, or a release while the hands still moved apart all swept).
`selectionEngine.sweepInputFor` + `sweepStep`:

- **Arm:** only an open palm held still for `ARM_MS` starts a sweep (and re-arms after one).
- **Open throughout:** if the sweeping hand stops being an open palm, the sweep is cancelled.
- **One thing at a time:** no sweep while zooming, while the primary hand pinches or makes a fist,
  or in the snapshot pose; and none for `BLOCK_COOLDOWN_MS` after any of them.

`hooks/handsPipeline.test.ts` drives real landmark sequences through the engine and
`selectionTick` (exactly what Hologram runs); its zoom tests fail with the v3.0 sweep put back.

**v3.3: either hand sweeps, however it is held** (user report: isolating by hand "not working at
all"). Replaying six real situations through the engine showed only one ever swept: roles came
from MediaPipe's Left/Right labels (swapped with mirrored cameras; a lone hand was always the
steering one), "open" needed fingers pointing up (a sideways, wiping palm never was), and any
curled steering hand blocked. Now:

- `gestureEngine` picks the sweeping hand by what it does (`Frame.sweep`): the open palm (with
  two, the one not steering), sticky while it stays open (`STICKY` 0.2), reported closed for one
  frame. "Open" is `openPalm`: 3 of 4 fingers reach further from the wrist than their middle
  joint, not pinching, at any angle. Navigation poses (`classifyPose`) are unchanged.
- Only a real drag or zoom (`grabbing`, `zooming`) or the snapshot pose blocks a sweep.
- A palm jump of more than `JUMP` (0.15) in one frame is the other hand taking over: the old
  hand counts as lost (a side exit still commits), the new one starts fresh.
- The palm marker shows the chosen hand, with a ring filling while it holds still (`arming`).
- `handsPipeline.test.ts` covers all six situations as landmark sequences.

**Operator lock (2026-10-18, user report: in a crowded room "it takes some other hand as mine").**
MediaPipe now detects up to 4 hands; `hooks/operatorLock.ts` (pure, unit-tested) passes on at most
two, the operator's. User decision: "nearest, then follow". The largest (nearest) hand is
acquired, followed frame to frame (`MAX_STEP` 0.15 + `DRIFT` 0.5/s unseen, capped `MAX_DRIFT`
0.35; size within 0.7 to 1.43x), joined by the same person's other hand (`JOIN` 0.65 to 1.55x
size, within `REACH` 16 palm lengths, the opposite Left/Right label), and released after `LOST_MS`
1.5 s unseen. **Two hands or fewer pass through exactly as before** (by construction; every
`handsPipeline` scenario asserts it); only with more than two in view are others ignored. Known
gap, as before: one stranger's hand plus one of the operator's (two hands in all) cannot be told
from one person's two without whole-body tracking. The status pill says "Following you" while
others are ignored.

**The AI snapshot** is detected in the gesture engine (both hands in the point pose, index tips
within `SNAP_DX`/`SNAP_DY`, once per touch) and suppresses the hold and the sweep.
`lib/snapshot.ts` decides: a patient reconstruction is never read (hard rule 8); on the atlas it
needs a selected part; a HUD line says whether the AI is on (no `GROQ_API_KEY` = off).

Hold and mouse share one selection model. A hold resolves through `resolveDwell` (a part selects
it, empty space steps back: isolated -> selected -> nothing); a mouse click through
`resolveClick` (as v2: the selected part clicked again isolates it). The panel's Isolate / Show
all button does the same as the sweep; a mouse or button isolate flies the rest off to the right.

**The removal animation follows the hand.** The sweep's screen offset becomes a world delta along
the camera's right and up vectors, scaled by the visible view size at the model's depth, and each
unselected mesh moves by it in its own parent frame (so the motion is right whatever the model's
rotation and the GLB nesting). Past `COMMIT` it flies on 1.2 view widths in the sweep's
direction and fades out; bringing it back retraces the same path.

**Constants kept from v1** (tuned against real hand tracking; do not change without a webcam):

```
SMOOTH     = 0.55     low-pass filter on landmarks
PINCH_ON   = 0.05     hysteresis: activate below
PINCH_OFF  = 0.075    hysteresis: deactivate above
ROT_SENS_Y = 3.2      ROT_SENS_X = 2.2
PAN_SENS   = 5.0
ZOOM_SENS  = 3.5      exponential
SCALE_MIN  = 0.08     SCALE_MAX  = 60.0
```

`DEAD_ZONE = 0.03` (v2): travel before a pinch or fist becomes a drag; the dead-zone travel itself
is never applied.

**New in v3, first guesses, to be tuned with a webcam** (`selectionEngine.ts`):

```
DWELL_MS       = 2000   hold to select (the user chose 1.5 s, then 2 s)
DWELL_GRACE_MS = 200    a hover flicker at a part's edge shorter than this keeps the hold
COMMIT         = 0.12   SIDEWAYS sweep (fraction of the screen) that isolates / restores (v3.2;
                        v3.1 was 0.18 in any direction: a dropped hand must never commit)
EXIT_MIN       = 0.05   (v3.2) an armed hand the camera loses commits if it had swept this far,
EDGE           = 0.2    mostly sideways, last seen within EDGE of the side it swept toward
                        (the hand ran out of camera range before committing: user report)
JITTER         = 0.02   sweep travel ignored before the rest starts to follow
STILL          = 0.025  ARM_MS = 400: an open palm held this still this long arms a sweep (v3.3; was
                        0.015, tighter than a real hand's sway); it arms at the averaged rest spot
                        (also re-arms after one, and springs back an unfinished one)
BLOCK_COOLDOWN_MS = 800 (v3.1) no sweep this long after a zoom, a grab or the snapshot pose
```

**Pinch hysteresis** (separate on/off thresholds) prevents flicker at the boundary.

**A fist outranks a pinch**: in a closed hand the thumb often rests on the index finger.

**Edge-triggering**: a hold fires once, then not again until the target changes or the point
ends; a sweep commits once, then disarms until the palm rests again (so one long sweep never both
clears and restores). Released early (lowered, curled, or still for `ARM_MS`), the rest springs
back. The snapshot fires once per touch (a counter in `useGestureControl`, consumed once).

**Selection needs the point pose**, so a resting open palm never selects anything; a pinching
other hand is zoom, never a sweep.

**Priority order:** two-hand zoom -> grab drag (pinch / fist) -> snapshot -> armed other-hand
sweep -> point and hold -> rest.

**The guide** (`components/HandGuide.tsx`, moves in `data/guideMoves.ts`, unit-tested): a 3D hand
(MediaPipe's 21 joints, `data/handPoses.ts`) acting each move on a stand-in body, in a plain
three.js scene owned by one effect. It opens by itself the first time hands come on
(`localStorage` `vesalius.handGuideSeen`, guarded), and from "How to use".

**Interpolation factors** in `AnatomicalModel`: rotation 0.12, position 0.22, scale 0.18.
These make gesture input feel responsive without jitter.

---

## Component responsibilities

| File | Owns |
|---|---|
| `App.tsx` | `selectedMesh`, `hoveredMesh`, `partsRemoved`, AI state. Cancels in-flight streams via `activeRequestRef` on selection change |
| `HologramCanvas.tsx` | R3F canvas (transparent: the hall is CSS behind it), OrbitControls |
| `HallStage.tsx` | The landing hall's studio lights, a local environment map (RoomEnvironment, nothing fetched), the pedestal and orbit ring; never raycast |
| `AnatomicalModel.tsx` | Per-frame loop: transform lerping, raycasting, selection edges, mesh animation |
| `SystemLayer.tsx` | Loads one GLB, applies holographic material per mesh, reports bounding box |
| `HandTracker.tsx` | MediaPipe init (`numHands: 4`), RAF detection loop, the operator lock, skeleton overlay (others' hands faint grey) |
| `operatorLock.ts` | Pure: whose hands to follow in a crowd (`lockStep`) |
| `gestureEngine.ts` | Pure: landmark smoothing, poses, the drag state machine, the other hand, `resolveClick` |
| `selectionEngine.ts` | Pure: hold to select (`dwellStep`, `resolveDwell`), sweep to clear (`sweepStep`), the per-frame `selectionTick` Hologram runs |
| `useGestureControl.ts` | Wraps the engine; one rendered `GestureState` per camera frame |
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

**Changed in phase three (user decision, 2026-09-27: "surgical green everywhere"); the room
redesigned 2026-10-18 (user request: "like the website") as the landing's hall.** Source:
`viewer/src/styles/tokens.css` (tokens) and `viewer/src/styles/room.css` (the room's look, scoped to
`.room`, loaded last; Holoscan's HUD markup and token names stay, restyled).

```css
/* the scan room (hologram + full body): the landing hall */
--room-bg:    #060b09    /* the hall's green-black; --room-glow adds the spotlight behind the anatomy */
--room-mint:  #c9f2df    /* single accent */
--room-ink:   #e8eee9
--room-coral: #ff7a66    /* frozen / refused / error only */
--sans: 'Geist'   --mono: 'Geist Mono'   --serif: 'Playfair Display' italic (bundled, OFL)
```

- **Type:** Geist in normal case; mono only for numbers; Playfair italic only for a structure's
  name in the part card (as anatomical plates set names). No letter-spaced mono-caps labels.
- **Shape:** panels 16px, inner tiles 12px, buttons and chips full pill (as the app).
- **Layout:** one glass panel on the left (title, a one-line summary, a "Face removed" badge when
  S7 applied, the systems as switches, details in a disclosure), the part card on the right (a
  bottom sheet on phones), the hint as a floating pill, no full-width bars.
- **The stage** (`HallStage`): the landing's light rig, a matte pedestal with a static soft
  shadow (not ContactShadows, which re-renders the scene every frame), and a mint orbit ring with
  a slow bead (still under reduced motion). It replaces the grid. It goes with the body
  (`lib/stageFollow.ts`): the model group (`name="anatomy-model"`, `userData.fit` = its auto-fit)
  carries the hand pan and zoom, so the stand takes those, never the turn; tipped past 0.45 rad
  it fades out by 0.75 rad.
- **Anatomy colours** (`layerStore.GROUP_COLOURS`) belong to the layer system, not the chrome.
  They form an anatomical palette (ivory bone, deep red vessels, coral organs, yellow nerves...),
  checked with the dataviz palette validator on `#060b09` over every pair that can be on screen
  together: normal-vision floor PASS (worst 15.5) and CVD separation PASS (worst 8.0). Deliberate
  exceptions: ivory bone sits outside the chart lightness band, and vessels are 2.99:1. Both are
  relieved by the names always shown. Any change re-runs the validator. Emissive = the colour
  itself (a selected part glows in its own colour, at 0.45 +/- 0.1, never neon).

Mint is the **only** accent in the scan room. Icons are Phosphor (no emoji). Everything else in
this document (rendering settings, gestures, z-index, raycasting) is unchanged.

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

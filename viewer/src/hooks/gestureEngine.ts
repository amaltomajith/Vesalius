// The hologram's hand-gesture engine, as pure functions of the landmarks (no React, no three.js),
// so every rule here is unit-tested (gestureEngine.test.ts).
//
// The model (gestures v3, user decisions 2026-10-18; refs/VIEWER-CONSTRAINTS.md):
//   open palm          rest: nothing moves
//   point              a cursor at the index tip; holding it on a part selects it (selectionEngine)
//   pinch and drag     turn the model
//   fist and drag      move the model
//   both hands pinch   zoom
//   an open palm       either hand, chosen by what it does (v3.3, `sweep`): held still then
//                      swept, it clears or brings back the unselected parts (selectionEngine)
//   both index fingers pointing at each other: the AI snapshot (`snapshot`, once per touch)
// The primary hand is the right if visible, else the left. There is no freeze mode.

export interface NormalizedLandmark { x: number; y: number; z: number }
export type Pt = { x: number; y: number };
export type Pose = 'open' | 'point' | 'pinch' | 'fist' | 'other';
export type ActiveGesture = 'ROTATE' | 'PAN' | 'ZOOM' | 'POINT' | 'PINCH' | 'REST' | 'NONE';

// ── Tuned on real hand tracking: unchanged from gestures v1 (VIEWER-CONSTRAINTS) ──────────────
export const SMOOTH     = 0.55;   // low-pass filter on landmarks
export const PINCH_ON   = 0.05;   // hysteresis: activate below
export const PINCH_OFF  = 0.075;  // hysteresis: release above
export const ROT_SENS_Y = 3.2;
export const ROT_SENS_X = 2.2;
export const PAN_SENS   = 5.0;
export const ZOOM_SENS  = 3.5;    // exponential
export const SCALE_MIN  = 0.08;
export const SCALE_MAX  = 60.0;

// ── New in v2: a first guess, to be tuned with a webcam in front of the screen ────────────────
export const DEAD_ZONE  = 0.03;   // normalised travel before a pinch / fist becomes a drag

// ── the AI snapshot: both index tips this close (Holoscan's original box, unchanged) ─────────
export const SNAP_DX = 0.15;
export const SNAP_DY = 0.08;

// ── the sweeping hand (v3.3) ──────────────────────────────────────────────────────────────────
export const STICKY = 0.2;       // the sweeping palm is the visible palm nearest its last spot, this close

// ── geometry ──────────────────────────────────────────────────────────────────────────────────
const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

export function smoothHand(raw: NormalizedLandmark[], prev?: NormalizedLandmark[]): NormalizedLandmark[] {
  if (!prev || prev.length !== raw.length) return raw;
  return raw.map((lm, i) => ({
    x: SMOOTH * prev[i].x + (1 - SMOOTH) * lm.x,
    y: SMOOTH * prev[i].y + (1 - SMOOTH) * lm.y,
    z: SMOOTH * prev[i].z + (1 - SMOOTH) * lm.z,
  }));
}

/** The index fingertip, mirrored so it matches the screen (the camera image is a mirror). */
export const indexTip = (lms: NormalizedLandmark[]): Pt => ({ x: 1 - lms[8].x, y: lms[8].y });

/** Palm centre: wrist + the four knuckles (stable, low jitter), mirrored. */
export function palmCenter(lms: NormalizedLandmark[]): Pt {
  const pts = [lms[0], lms[5], lms[9], lms[13], lms[17]];
  return { x: 1 - pts.reduce((s, p) => s + p.x, 0) / 5, y: pts.reduce((s, p) => s + p.y, 0) / 5 };
}

/** Thumb tip and index tip, halfway, mirrored: the point a pinch holds. */
export const pinchPoint = (lms: NormalizedLandmark[]): Pt => ({ x: 1 - (lms[4].x + lms[8].x) / 2, y: (lms[4].y + lms[8].y) / 2 });

// A finger is extended when its tip is above both its middle joint and its knuckle (MediaPipe y
// grows downward).
const FINGERS: [tip: number, pip: number, mcp: number][] = [[8, 7, 5], [12, 11, 9], [16, 15, 13], [20, 19, 17]];
const extended = (lms: NormalizedLandmark[]) => FINGERS.map(([t, p, m]) => lms[t].y < lms[p].y && lms[t].y < lms[m].y);

/** How clenched the hand is: mean fingertip distance to the palm centre, over palm length. */
function clenchRatio(lms: NormalizedLandmark[]): number {
  const palmLength = dist(lms[0], lms[9]);
  if (palmLength === 0) return 1;
  const pts = [lms[0], lms[5], lms[9], lms[13], lms[17]];
  const c = { x: pts.reduce((s, p) => s + p.x, 0) / 5, y: pts.reduce((s, p) => s + p.y, 0) / 5 };
  const tips = [lms[8], lms[12], lms[16], lms[20]];
  return tips.reduce((s, t) => s + dist(t, c), 0) / 4 / palmLength;
}

/** An open palm at any angle (v3.3, for the sweep only): at least 3 of the 4 fingers reach
 *  further from the wrist than their middle joint, and the hand is not pinching. The navigation
 *  poses (classifyPose) still use the upright test; this one also holds for a sideways, wiping
 *  hand, which the upright test never called open (found replaying a sweep through the engine). */
const FINGER_TIP_PIP: [tip: number, pip: number][] = [[8, 6], [12, 10], [16, 14], [20, 18]];
export function openPalm(lms: NormalizedLandmark[], isPinching: boolean): boolean {
  if (isPinching) return false;
  const reach = FINGER_TIP_PIP.filter(([t, p]) => dist(lms[t], lms[0]) > 1.1 * dist(lms[p], lms[0])).length;
  return reach >= 3;
}

export const isFist = (lms: NormalizedLandmark[]) => extended(lms).every((e) => !e) && clenchRatio(lms) < 0.9;

/** Pinch with hysteresis: `wasPinching` is this hand's state on the previous frame. */
export function pinching(lms: NormalizedLandmark[], wasPinching: boolean): boolean {
  const d = dist(lms[4], lms[8]);
  return wasPinching ? d < PINCH_OFF : d < PINCH_ON;
}

/** One pose per hand per frame. A fist outranks a pinch: in a closed hand the thumb often rests
 *  on the index finger, and that is a grab, not a click. */
export function classifyPose(lms: NormalizedLandmark[], isPinching: boolean): Pose {
  if (isFist(lms)) return 'fist';
  if (isPinching) return 'pinch';
  const [index, ...others] = extended(lms);
  if (index && others.every((e) => !e)) return 'point';
  if (extended(lms).filter(Boolean).length >= 3) return 'open';
  return 'other';
}

// ── the state machine ─────────────────────────────────────────────────────────────────────────
interface Grab {
  kind: 'pinch' | 'fist';
  hand: 'right' | 'left';
  startT: number;
  start: Pt;          // where the grab began (pinch point or palm centre)
  last: Pt;           // last applied position, once dragging
  dragging: boolean;
}

export interface EngineState {
  smooth: { right?: NormalizedLandmark[]; left?: NormalizedLandmark[] };
  pinch: { right: boolean; left: boolean };
  grab: Grab | null;
  zoom: { dist: number; baseScale: number } | null;
  snapTouch: boolean;   // the snapshot pose was held on the previous frame (fires once per touch)
  sweepAt: Pt | null;   // the palm that is sweeping: it stays the sweeping hand while it is open
  pos: Pt;
  rotX: number;
  rotY: number;
  scale: number;
}

export const initialEngine = (): EngineState => ({
  smooth: {}, pinch: { right: false, left: false }, grab: null, zoom: null, snapTouch: false, sweepAt: null,
  pos: { x: 0, y: 0 }, rotX: 0, rotY: 0, scale: 1,
});

export interface Frame {
  gesture: ActiveGesture;
  pose: Pose | null;
  cursor: Pt | null;           // the primary hand's index tip, mirrored, 0..1
  grabbing: boolean;           // a drag or zoom is steering the model
  zooming: boolean;            // both hands pinching
  /** the other hand's palm centre (mirrored, 0..1) while it is up, whatever its pose; null otherwise */
  second: Pt | null;
  secondPose: Pose | null;
  /** the hand that can sweep (v3.3): an open palm, either hand, picked by what it does, not its
   *  label; `open` false on the frame it closes; null when no hand is an open palm */
  sweep: { at: Pt; open: boolean } | null;
  /** both index fingers pointing at each other, now (suppresses the hold and the sweep) */
  snapPose: boolean;
  /** the snapshot fires: true on the first frame of each touch only */
  snapshot: boolean;
  tips: { right?: Pt; left?: Pt };
  handsVisible: boolean;
}

/** Advance one camera frame. `hands` and `handedness` are MediaPipe's, per detected hand. */
export function step(prev: EngineState, hands: NormalizedLandmark[][] | null, handedness: string[] | undefined, now: number):
  { state: EngineState; frame: Frame } {
  const s: EngineState = { ...prev, smooth: { ...prev.smooth }, pinch: { ...prev.pinch }, pos: { ...prev.pos } };
  const idle: Frame = {
    gesture: 'NONE', pose: null, cursor: null, grabbing: false, zooming: false, second: null, secondPose: null, sweep: null,
    snapPose: false, snapshot: false, tips: {}, handsVisible: false,
  };

  if (!hands || hands.length === 0) {
    // hands gone: let go of everything
    return { state: { ...s, smooth: {}, pinch: { right: false, left: false }, grab: null, zoom: null, snapTouch: false, sweepAt: null }, frame: idle };
  }

  // assign hands: by MediaPipe's label, else first = right
  let rightRaw: NormalizedLandmark[] | undefined;
  let leftRaw: NormalizedLandmark[] | undefined;
  hands.forEach((h, i) => {
    const label = handedness?.[i] ?? '';
    if (label === 'Right') rightRaw = h;
    else if (label === 'Left') leftRaw = h;
  });
  if (!rightRaw && !leftRaw) { rightRaw = hands[0]; leftRaw = hands[1]; }

  const right = rightRaw ? smoothHand(rightRaw, s.smooth.right) : undefined;
  const left = leftRaw ? smoothHand(leftRaw, s.smooth.left) : undefined;
  s.smooth = { right, left };
  s.pinch = { right: right ? pinching(right, s.pinch.right) : false, left: left ? pinching(left, s.pinch.left) : false };

  const tips = { right: right ? indexTip(right) : undefined, left: left ? indexTip(left) : undefined };
  const base = { tips, handsVisible: true, snapshot: false };

  // ── 1. zoom: both hands pinching (highest priority) ──
  if (right && left && s.pinch.right && s.pinch.left) {
    const a = pinchPoint(right), b = pinchPoint(left);
    const d = dist(a, b);
    s.grab = null;
    s.snapTouch = false;
    s.sweepAt = null;
    if (!s.zoom) {
      s.zoom = { dist: d, baseScale: s.scale };
    } else {
      const next = Math.max(SCALE_MIN, Math.min(SCALE_MAX, s.zoom.baseScale * Math.exp((d - s.zoom.dist) * ZOOM_SENS)));
      const ratio = next / s.scale;
      // pivot about the midpoint of the two pinches, in scene units
      const hx = ((a.x + b.x) / 2 - 0.5) * 6.5, hy = (0.5 - (a.y + b.y) / 2) * 4.5;
      s.pos = { x: s.pos.x + (1 - ratio) * (hx - s.pos.x), y: s.pos.y + (1 - ratio) * (hy - s.pos.y) };
      s.scale = next;
    }
    return { state: s, frame: {
      ...base, gesture: 'ZOOM', pose: 'pinch', cursor: null, grabbing: true, zooming: true,
      second: null, secondPose: 'pinch', sweep: null, snapPose: false,
    } };
  }
  s.zoom = null;

  // ── 2. the primary hand steers; the other hand is reported with its pose (sweepStep decides) ──
  const which: 'right' | 'left' = right ? 'right' : 'left';
  const h = (right ?? left)!;
  const other = which === 'right' ? left : undefined;
  const second = other ? palmCenter(other) : null;
  const secondPose = other ? classifyPose(other, s.pinch.left) : null;
  const pose = classifyPose(h, s.pinch[which]);
  const cursor = indexTip(h);

  // the AI snapshot: both hands pointing, index tips together; fires once per touch
  const snapPose = pose === 'point' && secondPose === 'point' && !!tips.right && !!tips.left
    && Math.abs(tips.right.x - tips.left.x) < SNAP_DX && Math.abs(tips.right.y - tips.left.y) < SNAP_DY;
  const snapshot = snapPose && !s.snapTouch;
  s.snapTouch = snapPose;

  // the sweeping hand (v3.3): by what it does, never by MediaPipe's label (which can be swapped,
  // and a lone hand was always "primary"). The palm already sweeping keeps the role while open;
  // else the one open palm; with two, the one that is not steering.
  const seen = [
    { lms: h, steering: true, pinch: s.pinch[which] },
    ...(other ? [{ lms: other, steering: false, pinch: s.pinch.left }] : []),
  ].map((c) => ({ at: palmCenter(c.lms), open: openPalm(c.lms, c.pinch), steering: c.steering }));
  let sweep: { at: Pt; open: boolean } | null = null;
  const lastSweep = s.sweepAt;
  if (lastSweep) {
    const near = seen.reduce((a, b) => (dist(a.at, lastSweep) <= dist(b.at, lastSweep) ? a : b));
    if (dist(near.at, lastSweep) < STICKY) sweep = { at: near.at, open: near.open };  // closed: open false, once
  }
  if (!sweep) {                       // (a sweeping palm that just closed reports so for one frame)
    const opens = seen.filter((c) => c.open);
    const pick = opens.length === 1 ? opens[0] : opens.find((c) => !c.steering);
    if (pick) sweep = { at: pick.at, open: true };
  }
  s.sweepAt = sweep?.open ? sweep.at : null;
  const hands2 = { zooming: false, second, secondPose, sweep, snapPose, snapshot };

  // a grab ends when its pose ends or the steering hand changes
  if (s.grab && (s.grab.hand !== which || pose !== s.grab.kind)) s.grab = null;

  if (pose === 'pinch' || pose === 'fist') {
    const at = pose === 'pinch' ? pinchPoint(h) : palmCenter(h);
    if (!s.grab) {
      s.grab = { kind: pose, hand: which, startT: now, start: at, last: at, dragging: false };
    } else if (!s.grab.dragging && dist(at, s.grab.start) > DEAD_ZONE) {
      s.grab = { ...s.grab, dragging: true, last: at }; // start from here: the dead-zone travel is not applied
    } else if (s.grab.dragging) {
      const dx = at.x - s.grab.last.x, dy = at.y - s.grab.last.y;
      if (pose === 'pinch') {
        s.rotY += dx * ROT_SENS_Y;
        s.rotX = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, s.rotX + dy * ROT_SENS_X));
      } else {
        s.pos = { x: s.pos.x + dx * PAN_SENS, y: s.pos.y - dy * PAN_SENS };
      }
      s.grab = { ...s.grab, last: at };
    }
    const dragging = s.grab.dragging;
    const gesture: ActiveGesture = dragging ? (pose === 'pinch' ? 'ROTATE' : 'PAN') : pose === 'pinch' ? 'PINCH' : 'NONE';
    // a closed hand has no cursor: aiming is the pointing finger's job
    return { state: s, frame: { ...base, ...hands2, gesture, pose, cursor: null, grabbing: dragging } };
  }

  const gesture: ActiveGesture = pose === 'open' ? 'REST' : pose === 'point' ? 'POINT' : 'NONE';
  return { state: s, frame: { ...base, ...hands2, gesture, pose, cursor, grabbing: false } };
}

// ── what a click does (hands and mouse alike) ────────────────────────────────────────────────
export interface Selection { selected: string | null; isolated: boolean }

/** Click on a part (its name) or on empty space (null). One level at a time:
 *  nothing -> selected -> isolated, and empty space steps back the same way. */
export function resolveClick(sel: Selection, target: string | null): Selection {
  if (target === null) return sel.isolated ? { selected: sel.selected, isolated: false } : { selected: null, isolated: false };
  if (target === sel.selected) return { selected: target, isolated: true };
  return { selected: target, isolated: false };
}

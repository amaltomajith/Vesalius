// Selecting and clearing with the hands (gestures v3, user decisions 2026-10-18), as pure
// functions of time, so every rule is unit-tested (selectionEngine.test.ts):
//   point and hold on a part (2 s)      select it
//   point and hold on empty space        step back: isolated -> selected -> nothing
//   the other hand: open palm, held      the unselected parts follow it; far enough and they
//   still a moment (armed), then swept   fly off that way (isolate); sweep again to bring them
//                                        back (restore)
// v3.1: a sweep has to be meant. It starts only from an armed open palm, cancels if the hand stops
// being open, and is off during (and just after) a zoom, a grab or the snapshot pose, so zooming
// into an isolated part can never bring the body back.
// v3.2: a shorter sweep (0.12 of the screen), and a hand that leaves the camera through the side it
// was sweeping toward commits, as if it threw the parts off screen (user report: the hand ran out
// of camera range before the sweep committed, and the parts sprang back). Lowering the hand, or
// losing it mid-frame, still cancels. A commit needs the SIDEWAYS travel to reach COMMIT (the fly
// direction keeps the full diagonal): with the shorter distance, a dropped hand would otherwise
// clear or restore by accident.
// v3.3 (user report: isolating by hand "not working at all"): either hand sweeps, picked by what
// it does (gestureEngine `sweep`: an open palm at any angle, not a handedness label); only a real
// drag or zoom blocks, not a resting curled hand; STILL allows a real hand's sway.
// Hands and mouse share the selection model (gestureEngine.resolveClick for clicks).

import type { Pose, Pt } from './gestureEngine';
import type { Selection } from './gestureEngine';

// first guesses, to be tuned with a webcam in front of the screen
export const DWELL_MS = 2000;       // hold a point this long to select (user choice: 1.5 s, then 2 s)
export const DWELL_GRACE_MS = 200;  // a hover flicker at a part's edge shorter than this is ignored
export const COMMIT = 0.12;         // sideways sweep (fraction of the screen) that clears / restores (v3.1: 0.18, any way)
export const JITTER = 0.02;         // sweep movement smaller than this is the hand holding still
export const STILL = 0.025;         // the palm counts as still while it stays within this... (v3.1: 0.015)
export const ARM_MS = 400;          // ...for this long: armed, a sweep can start from where it is
export const BLOCK_COOLDOWN_MS = 800; // after a zoom, a grab or the snapshot pose, no sweep for this long
export const EXIT_MIN = 0.05;       // a hand lost mid-sweep commits if it had swept at least this far...
export const EDGE = 0.2;            // ...mostly sideways, last seen within this of the side it swept toward
export const JUMP = 0.15;           // the palm moving this far in one frame is a different hand taking over

const len = (p: Pt) => Math.hypot(p.x, p.y);

// ── point and hold ────────────────────────────────────────────────────────────────────────────
export interface DwellState {
  holding: boolean;
  target: string | null;   // the part being held on (null: empty space)
  since: number;           // when this hold began
  lostAt: number | null;   // when the cursor left the target (grace period)
  fired: boolean;          // this hold has already selected; no repeat until it ends
}

export const initialDwell = (): DwellState => ({ holding: false, target: null, since: 0, lostAt: null, fired: false });

/** One frame of the hold. `pointing`: the primary hand is in the point pose with a cursor;
 *  `target`: what the cursor is on (a structure's name, or null for empty space). */
export function dwellStep(d: DwellState, input: { pointing: boolean; target: string | null }, now: number):
  { state: DwellState; progress: number; fire: { target: string | null } | null } {
  if (!input.pointing) return { state: initialDwell(), progress: 0, fire: null };

  let s = d;
  if (!s.holding) {
    s = { holding: true, target: input.target, since: now, lostAt: null, fired: false };
  } else if (input.target === s.target) {
    if (s.lostAt !== null) s = { ...s, lostAt: null };
  } else if (s.lostAt === null) {
    s = { ...s, lostAt: now };                                    // left the target: grace begins
  } else if (now - s.lostAt > DWELL_GRACE_MS) {
    s = { holding: true, target: input.target, since: now, lostAt: null, fired: false }; // a new hold
  }

  if (s.fired) return { state: s, progress: 0, fire: null };
  const held = now - s.since;
  if (held >= DWELL_MS) return { state: { ...s, fired: true }, progress: 1, fire: { target: s.target } };
  return { state: s, progress: held / DWELL_MS, fire: null };
}

/** What a completed hold does: on a part, select it (holding on the selection changes nothing);
 *  on empty space, step back one level. */
export function resolveDwell(sel: Selection, target: string | null): Selection {
  if (target === null) return sel.isolated ? { selected: sel.selected, isolated: false } : { selected: null, isolated: false };
  if (sel.isolated || target === sel.selected) return sel;
  return { selected: target, isolated: false };
}

// ── the other hand's sweep ────────────────────────────────────────────────────────────────────
export interface SweepState {
  anchor: Pt | null;       // armed: where this sweep starts (null: not armed)
  stillAt: Pt | null;      // where the hand last came to rest (the fixed reference for "has it moved")
  restAt: Pt | null;       // the average of where it has been since: the spot a sweep starts from
  stillSince: number;
  cooldownUntil: number;   // no arming before this (a zoom, grab or snapshot just happened)
  last: Pt | null;         // where the armed hand was last seen (a side exit commits from here)
}

export const initialSweep = (): SweepState => ({ anchor: null, stillAt: null, restAt: null, stillSince: 0, cooldownUntil: 0, last: null });

/** The armed hand was lost: did it leave through the side it was sweeping toward? */
function sideExit(st: SweepState): Pt | null {
  if (!st.anchor || !st.last) return null;
  const d = { x: st.last.x - st.anchor.x, y: st.last.y - st.anchor.y };
  const dist = len(d);
  if (dist < EXIT_MIN || Math.abs(d.x) < Math.abs(d.y)) return null;           // too short, or not sideways
  const atEdge = d.x > 0 ? st.last.x >= 1 - EDGE : st.last.x <= EDGE;            // the side it was heading for
  return atEdge ? { x: d.x / dist, y: d.y / dist } : null;
}

export interface SweepFrame {
  state: SweepState;
  /** live displacement of the other hand from where the sweep began, in screen fractions */
  offset: Pt;
  live: boolean;
  /** the open palm is armed: moving it now sweeps */
  armed: boolean;
  /** 0..1 while an open palm holds still towards arming (the marker's ring); 1 once armed */
  arming: number;
  event: { kind: 'isolate' | 'restore'; dir: Pt } | null;
}

export interface SweepInput {
  second: Pt | null;    // the other hand's palm centre (mirrored), or null
  secondOpen: boolean;  // it is an open palm
  blocked: boolean;     // a zoom, the primary hand grabbing, or the snapshot pose
  active: boolean;      // something is selected (otherwise a sweep means nothing)
  isolated: boolean;    // the rest is currently cleared
}

/** One frame of the other hand. Arms after ARM_MS still as an open palm; then the rest follows it
 *  and past COMMIT it isolates (or restores); afterwards it must rest again to re-arm. */
export function sweepStep(st: SweepState, input: SweepInput, now: number): SweepFrame {
  const zero = { x: 0, y: 0 };
  const off = (state: SweepState, arming = 0): SweepFrame => ({ state, offset: zero, live: false, armed: false, arming, event: null });
  if (input.blocked) return off({ ...initialSweep(), cooldownUntil: now + BLOCK_COOLDOWN_MS });
  // a jump no hand can make in one frame: the sweep has passed to the other hand (either hand can
  // sweep, v3.3). The old hand is treated as lost (a side exit still counts); the new one starts fresh.
  const handOver = !!input.second && !!st.last && len({ x: input.second.x - st.last.x, y: input.second.y - st.last.y }) > JUMP;
  if (handOver) {
    const dir = input.active ? sideExit(st) : null;
    const fresh = { ...initialSweep(), cooldownUntil: st.cooldownUntil };
    return dir
      ? { state: fresh, offset: zero, live: false, armed: false, arming: 0, event: { kind: input.isolated ? 'restore' : 'isolate', dir } }
      : off(fresh);
  }
  // the armed hand left the camera mid-sweep: through the side it swept toward, that is the sweep
  if (!input.second && input.active) {
    const dir = sideExit(st);
    if (dir) return { state: { ...initialSweep(), cooldownUntil: st.cooldownUntil }, offset: zero, live: false, armed: false,
      arming: 0, event: { kind: input.isolated ? 'restore' : 'isolate', dir } };
  }
  // no hand, not an open palm, or nothing selected: no sweep (the parts spring back)
  if (!input.second || !input.secondOpen || !input.active) return off({ ...initialSweep(), cooldownUntil: st.cooldownUntil });
  const p = input.second;

  let s = st;
  if (!s.stillAt || len({ x: p.x - s.stillAt.x, y: p.y - s.stillAt.y }) > STILL) {
    s = { ...s, stillAt: p, restAt: p, stillSince: now };
  } else {
    // still: settle the rest spot on where the hand actually is (it may have stopped a little past
    // the reference), so a trembling, resting palm is not read as 2% into a sweep
    const r = s.restAt ?? p;
    s = { ...s, restAt: { x: r.x + (p.x - r.x) * 0.25, y: r.y + (p.y - r.y) * 0.25 } };
  }
  // resting long enough (and not cooling down) arms where the hand came to rest: a fresh start, or
  // a spring back when it rested away from the anchor. A hand still near its anchor keeps it: a
  // sweep's first small step (under STILL) still counts as resting and must not move the start.
  const restingSince = Math.max(s.stillSince, s.cooldownUntil);
  const awayFromAnchor = !s.anchor || len({ x: p.x - s.anchor.x, y: p.y - s.anchor.y }) > STILL;
  if (now >= s.cooldownUntil && now - restingSince >= ARM_MS && awayFromAnchor) s = { ...s, anchor: s.restAt };
  const anchor = s.anchor;
  if (!anchor) return off(s, now < s.cooldownUntil ? 0 : Math.min(1, (now - restingSince) / ARM_MS));
  s = { ...s, last: p };

  const d = { x: p.x - anchor.x, y: p.y - anchor.y };
  const dist = len(d);
  if (dist < JITTER) return { state: s, offset: zero, live: false, armed: true, arming: 1, event: null };
  if (Math.abs(d.x) >= COMMIT) {
    const dir = { x: d.x / dist, y: d.y / dist };
    return { state: { ...s, anchor: null, last: null, stillAt: p, restAt: p, stillSince: now }, offset: zero, live: false, armed: false,
      arming: 0, event: { kind: input.isolated ? 'restore' : 'isolate', dir } };
  }
  return { state: s, offset: d, live: true, armed: true, arming: 1, event: null };
}

// ── what a hands frame means for the hold and the sweep (Hologram wires exactly these) ─────────
/** The fields of a gesture frame the selection reads (gestureEngine.Frame and GestureState). */
export interface HandsFrame {
  pose: Pose | null;
  cursor: Pt | null;
  zooming: boolean;
  /** a drag or zoom is steering the model */
  grabbing: boolean;
  /** the hand that can sweep: either hand, an open palm at any angle (gestureEngine, v3.3) */
  sweep: { at: Pt; open: boolean } | null;
  snapPose: boolean;
}

/** The hold counts only for a pointing primary hand, and never in the snapshot pose. */
export const holdPointing = (f: HandsFrame): boolean => f.pose === 'point' && !!f.cursor && !f.snapPose;

/** One thing at a time: no sweep while zooming, while a hand is actually dragging the model, or in
 *  the snapshot pose (v3.3: a curled or pinched hand that is not moving anything no longer blocks);
 *  only an open palm sweeps. */
export const sweepInputFor = (f: HandsFrame, sel: Selection): SweepInput => ({
  second: f.sweep?.at ?? null,
  secondOpen: f.sweep?.open ?? false,
  blocked: f.zooming || f.grabbing || f.snapPose,
  active: !!sel.selected,
  isolated: sel.isolated,
});

/** Everything the hands' selection keeps between frames. */
export interface HandsSelection { dwell: DwellState; sweep: SweepState; sel: Selection }

/** One camera frame of selecting with the hands: the hold first, then the other hand's sweep.
 *  `hovered` is the part under the cursor (null: empty space). Hologram runs exactly this. */
export function selectionTick(st: HandsSelection, f: HandsFrame, hovered: string | null, now: number):
  { state: HandsSelection; progress: number; sweep: SweepFrame } {
  const d = dwellStep(st.dwell, { pointing: holdPointing(f), target: hovered }, now);
  let sel = d.fire ? resolveDwell(st.sel, d.fire.target) : st.sel;
  const sw = sweepStep(st.sweep, sweepInputFor(f, sel), now);
  if (sw.event) sel = { selected: sel.selected, isolated: sw.event.kind === 'isolate' };
  return { state: { dwell: d.state, sweep: sw.state, sel }, progress: d.progress, sweep: sw };
}

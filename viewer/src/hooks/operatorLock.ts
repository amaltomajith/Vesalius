// Whose hands to follow when several people are in front of the camera (user report, 2026-10-18:
// "even though my hands are visible, it takes some other hand as mine"). A pure filter between
// MediaPipe and the gesture engine: of every hand detected, it keeps at most two, the operator's.
// User decision: "nearest, then follow". The person closest to the camera (the largest hands) is
// picked, then followed frame to frame while their hands stay in view; after LOST_MS without them,
// the nearest is picked again.
//
// Two hands or fewer in view pass through exactly as before (the user asked that nothing else
// change): one person's own two hands are not told apart from strangers' by size or distance
// (arms spread, or one hand pushed toward the camera, break any tight rule). The lock still tracks
// them, so it knows who the operator is when a crowd appears; with more than two hands in view it
// keeps only the operator's. Left as before: one stranger's hand plus one of the operator's (two
// hands in all) cannot be told from one person's two hands without whole-body tracking.

import type { NormalizedLandmark, Pt } from './gestureEngine';

// first guesses, to be tuned with a webcam and a crowd
export const MAX_STEP = 0.15;     // a tracked hand moves at most this far between frames (image units)...
export const DRIFT = 0.5;         // ...plus this much per second it went unseen (a short dropout)...
export const MAX_DRIFT = 0.35;    // ...but never more than this in all
export const SIZE_MIN = 0.7;      // a followed hand is this to...
export const SIZE_MAX = 1 / 0.7;  // ...this times its last size (it moves nearer or further only gradually)
export const JOIN_MIN = 0.65;     // the same person's other hand is this to...
export const JOIN_MAX = 1.55;     // ...this times the size of their hand (a stranger rarely is, and the label differs)
export const REACH = 16;          // the other hand is within this many palm lengths (arms spread)
export const LOST_MS = 1500;      // gone this long: the lock is released and the nearest is picked again

export interface DetectedHand { lms: NormalizedLandmark[]; label?: string }
interface Slot { at: Pt; size: number; label: string; seenAt: number }
export interface LockState { slots: Slot[] }
export const initialLock = (): LockState => ({ slots: [] });

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);
const palm = (lms: NormalizedLandmark[]): Pt => {
  const pts = [lms[0], lms[5], lms[9], lms[13], lms[17]];
  return { x: pts.reduce((s, p) => s + p.x, 0) / 5, y: pts.reduce((s, p) => s + p.y, 0) / 5 };
};
/** palm length, wrist to the middle knuckle: the nearer the person, the larger */
export const handSize = (lms: NormalizedLandmark[]) => dist(lms[0], lms[9]);
const sameScale = (a: number, b: number) => a / b >= SIZE_MIN && a / b <= SIZE_MAX;
const sameBody = (a: number, b: number) => a / b >= JOIN_MIN && a / b <= JOIN_MAX;

/** One camera frame: which of the detected hands go on to the gestures (indices, detection order),
 *  and how many others were ignored. */
export function lockStep(st: LockState, hands: DetectedHand[], now: number): { state: LockState; keep: number[]; ignored: number } {
  const seen = hands.map((h, i) => ({ i, at: palm(h.lms), size: handSize(h.lms), label: h.label ?? '' }));
  const slots = st.slots.filter((s) => now - s.seenAt <= LOST_MS);
  const ours = new Set<number>();
  const slotOf = (h: (typeof seen)[number]): Slot => ({ at: h.at, size: h.size, label: h.label, seenAt: now });

  // 1. follow: each tracked hand takes the nearest detection it could have moved to
  const pairs = slots.flatMap((s, k) => seen
    .filter((h) => sameScale(h.size, s.size) && dist(h.at, s.at) <= Math.min(MAX_DRIFT, MAX_STEP + DRIFT * (now - s.seenAt) / 1000))
    .map((h) => ({ k, h, d: dist(h.at, s.at) })))
    .sort((a, b) => a.d - b.d);
  const matched = new Set<number>();
  for (const { k, h } of pairs) {
    if (matched.has(k) || ours.has(h.i)) continue;
    matched.add(k); ours.add(h.i);
    slots[k] = slotOf(h);
  }

  // 2. acquire: nobody followed (none yet, or gone for LOST_MS) -> the nearest person's largest hand
  if (slots.length === 0 && seen.length > 0) {
    const centre = { x: 0.5, y: 0.5 };
    const first = [...seen].sort((a, b) => b.size - a.size || dist(a.at, centre) - dist(b.at, centre))[0];
    ours.add(first.i);
    slots.push(slotOf(first));
  }

  // 3. the same person's other hand joins a free slot, or one whose hand was not seen this frame:
  //    within an arm's reach of a hand seen this frame, of a similar size, and (labels known) the
  //    other hand, left for right
  const anchor = slots.find((s) => s.seenAt === now);
  const open = slots.length < 2 ? slots.length : slots.findIndex((s) => s.seenAt !== now);
  if (anchor && open >= 0) {
    const partner = seen
      .filter((h) => !ours.has(h.i) && sameBody(h.size, anchor.size) && dist(h.at, anchor.at) <= REACH * anchor.size
        && (!h.label || !anchor.label || h.label !== anchor.label))
      .sort((a, b) => dist(a.at, anchor.at) - dist(b.at, anchor.at))[0];
    if (partner) {
      ours.add(partner.i);
      slots[open] = slotOf(partner);
    }
  }

  // two hands or fewer: exactly as before (all of them); a crowd: only the operator's
  const keep = hands.length <= 2 ? hands.map((_, i) => i) : [...ours].sort((a, b) => a - b);
  return { state: { slots }, keep, ignored: hands.length - keep.length };
}

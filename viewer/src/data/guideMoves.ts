// The moves of the hand guide (components/HandGuide.tsx), each a pure function of loop time, so
// what every animation shows is unit-tested (components/HandGuide.test.ts).

import { FIST, OPEN, PINCH, POINT, lerpPose, type Pose3 } from './handPoses';

export type MoveKey = 'rest' | 'turn' | 'move' | 'zoom' | 'select' | 'clear' | 'back' | 'snapshot';

export const MOVES: { key: MoveKey; title: string; how: string; atlasOnly?: boolean }[] = [
  { key: 'rest', title: 'Rest', how: 'Open palm: nothing moves.' },
  { key: 'turn', title: 'Turn', how: 'Pinch your thumb and index finger, then drag.' },
  { key: 'move', title: 'Move', how: 'Make a fist, then drag.' },
  { key: 'zoom', title: 'Zoom', how: 'Pinch with both hands, then spread them apart.' },
  { key: 'select', title: 'Select', how: 'Point at a part and hold still for 2 seconds.' },
  { key: 'clear', title: 'Clear the rest', how: 'Open either palm and hold it still until it lights up, then sweep sideways. The rest follows it away.' },
  { key: 'back', title: 'Bring back', how: 'Palm still, then sweep sideways again. Or point and hold on empty space.' },
  { key: 'snapshot', title: 'AI snapshot', how: 'Point both index fingers at each other: the AI gets a picture of this view.', atlasOnly: true },
];

export const LOOP_S = 3.6;

// ── each move as a pure function of loop time u in [0, 1] ─────────────────────────────────────
type V3 = [number, number, number];
export interface GuideHand { pose: Pose3; at: V3; mirror?: boolean; tilt?: number }
export interface GuideFrame {
  hands: GuideHand[];
  model: { rotY: number; x: number; y: number; scale: number };
  ring: number;        // hold progress around the selected part, 0..1
  palmRing: number;    // the other hand arming (held still) before a sweep, 0..1
  flash: number;       // the snapshot's flash, 0..1
  selected: boolean;   // the selected part glows
  rest: { dx: number; dy: number; opacity: number }; // the body and the unselected parts
}

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ease = (x: number) => { const t = clamp01(x); return t * t * (3 - 2 * t); };
const RIGHT: V3 = [1.6, -0.85, 0.2];
const AIM: V3 = [1.65, 0.62, 0.4];   // a pointing hand, tilted, its fingertip on the selected part
const still = { rotY: 0, x: 0, y: 0, scale: 1 };
const shown = { dx: 0, dy: 0, opacity: 1 };

export function frameFor(key: MoveKey, u: number): GuideFrame {
  const grip = ease(u / 0.12) * (1 - ease((u - 0.86) / 0.1));   // closes, holds, opens
  const k = ease((u - 0.15) / 0.6);                             // the drag
  const back = 1 - ease((u - 0.9) / 0.1);                       // the model settles before the loop
  const base: GuideFrame = { hands: [], model: still, ring: 0, palmRing: 0, flash: 0, selected: false, rest: shown };
  switch (key) {
    case 'rest':
      return { ...base, hands: [{ pose: OPEN, at: [RIGHT[0], RIGHT[1] + 0.06 * Math.sin(u * Math.PI * 2), RIGHT[2]] }] };
    case 'turn':
      return { ...base, hands: [{ pose: lerpPose(OPEN, PINCH, grip), at: [RIGHT[0] - 0.25 + 0.6 * k, RIGHT[1], RIGHT[2]] }],
        model: { ...still, rotY: 1.7 * k * back } };
    case 'move':
      return { ...base, hands: [{ pose: lerpPose(OPEN, FIST, grip), at: [RIGHT[0] - 0.2 + 0.45 * k, RIGHT[1] + 0.25 * k, RIGHT[2]] }],
        model: { ...still, x: 0.45 * k * back, y: 0.25 * k * back } };
    case 'zoom': {
      const pose = lerpPose(OPEN, PINCH, grip);
      const spread = 0.45 * k;
      return { ...base, hands: [
        { pose, at: [1.2 + spread, -0.8, 0.2] },
        { pose, at: [-1.2 - spread, -0.8, 0.2], mirror: true },
      ], model: { ...still, scale: 1 + 0.45 * k * back } };
    }
    case 'select': {
      const ring = clamp01((u - 0.12) / 0.6);
      const done = u >= 0.72;
      return { ...base, hands: [{ pose: POINT, at: AIM, tilt: Math.PI / 2 }],
        ring: done ? 0 : ring, selected: done, rest: { ...shown, opacity: done ? 0.45 : 1 } };
    }
    case 'clear': {
      const palmRing = clamp01((u - 0.05) / 0.25);              // the palm held still: armed
      const sweep = ease((u - 0.32) / 0.33);
      const fly = ease((u - 0.65) / 0.2);
      return { ...base, selected: true, palmRing,
        hands: [
          { pose: POINT, at: AIM, tilt: Math.PI / 2 },
          { pose: OPEN, at: [-1.35 - 0.85 * sweep, -0.8, 0.3], mirror: true },
        ],
        rest: { dx: -0.85 * sweep - 2.6 * fly, dy: 0, opacity: 0.45 * (1 - 0.4 * sweep) * (1 - fly) } };
    }
    case 'back': {
      const palmRing = clamp01((u - 0.05) / 0.25);
      const sweep = ease((u - 0.32) / 0.45);
      return { ...base, selected: true, palmRing,
        hands: [
          { pose: POINT, at: AIM, tilt: Math.PI / 2 },
          { pose: OPEN, at: [-2.2 + 0.85 * sweep, -0.8, 0.3], mirror: true },
        ],
        rest: { dx: -3.45 * (1 - sweep), dy: 0, opacity: 0.45 * sweep } };
    }
    case 'snapshot': {
      // index tips sit 1.2 from each hand's origin once tilted; they meet when |x| = 1.25
      const k = ease((u - 0.1) / 0.35);
      const a = 2.0 - 0.75 * k;
      const flash = clamp01(1 - Math.abs(u - 0.52) / 0.07);
      return { ...base, selected: true, flash,
        hands: [
          { pose: POINT, at: [a, -0.95, 0.8], tilt: Math.PI / 2 },
          { pose: POINT, at: [-a, -0.95, 0.8], tilt: Math.PI / 2, mirror: true },
        ] };
    }
  }
}


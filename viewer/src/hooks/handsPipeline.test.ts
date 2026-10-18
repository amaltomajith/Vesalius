import { describe, expect, it } from 'vitest';
import { initialEngine, step, type Frame, type NormalizedLandmark, type Selection } from './gestureEngine';
import { ARM_MS, COMMIT, initialDwell, initialSweep, selectionTick, type SweepFrame } from './selectionEngine';
import { hand, repeat, rotate, scale } from './testHands';
import { initialLock, lockStep } from './operatorLock';

// Real landmark sequences through the gesture engine and the selection tick, wired the way the
// hologram wires them (screens/Hologram.tsx runs selectionTick on every camera frame). These are
// the regression tests for "zooming into the isolated part brings the body back" (gestures v3.1).

const FRAME = 16;
const LR = ['Right', 'Left'];
const ISOLATED: Selection = { selected: 'liver', isolated: true };
const SELECTED: Selection = { selected: 'liver', isolated: false };

function drive(frames: NormalizedLandmark[][][], start: Selection, hovered: string | null = null, labels: (i: number) => string[] = () => LR) {
  // the crowd filter (operatorLock) must leave one person's hands alone: every scenario here is one
  // person, so on every frame it has to keep every hand, i.e. the engine sees exactly what it did
  let lock = initialLock();
  frames.forEach((hands, i) => {
    const r = lockStep(lock, hands.map((lms) => ({ lms })), i * FRAME);
    lock = r.state;
    expect(r.keep.length, `operator lock dropped a hand at frame ${i}`).toBe(hands.length);
  });
  let engine = initialEngine();
  let hs = { dwell: initialDwell(), sweep: initialSweep(), sel: start };
  const out: Frame[] = [];
  const sweeps: SweepFrame[] = [];
  frames.forEach((hands, i) => {
    const r = step(engine, hands, labels(i), i * FRAME);
    engine = r.state;
    const t = selectionTick(hs, r.frame, hovered, i * FRAME);
    hs = t.state;
    out.push(r.frame);
    sweeps.push(t.sweep);
  });
  return { sel: hs.sel, frames: out, sweeps, events: sweeps.filter((s) => s.event).map((s) => s.event!) };
}

// camera x: a hand at camera x 0.45 is at screen x 0.55 (mirrored). Spreading = right hand's
// camera x falls while the left's rises.
const pinchPair = (spread: number, leftGap?: number) => [
  hand('pinch', 0.45 - spread / 2, 0.5),
  hand('pinch', 0.55 + spread / 2, 0.5, leftGap),
];
const LOOSE = 0.1; // a pinch whose thumb has drifted off the index: the hand reads as open

describe('zooming never sweeps (the user report)', () => {
  it('a zoom whose other pinch loosens mid-spread, the hand still moving out: no restore', () => {
    const frames = [
      ...repeat(15, (i) => pinchPair(i * 0.012)),
      ...repeat(40, (i) => pinchPair(0.18 + i * 0.015, LOOSE)), // pinch gone; each hand moves 0.3 more
    ];
    const r = drive(frames, ISOLATED);
    // guard: the dangerous moment really happens (the zoom ends, the other hand is an open palm)
    expect(r.frames.some((f) => f.zooming)).toBe(true);
    expect(r.frames.slice(-15).every((f) => !f.zooming && f.secondPose === 'open')).toBe(true);
    expect(r.events).toEqual([]);
    expect(r.sel).toEqual(ISOLATED);
  });

  it('both pinches released while the hands keep moving apart: no restore', () => {
    const frames = [
      ...repeat(15, (i) => pinchPair(i * 0.012)),
      ...repeat(30, (i) => [hand('open', 0.36 - i * 0.008, 0.5), hand('open', 0.64 + i * 0.008, 0.5)]),
    ];
    const r = drive(frames, ISOLATED);
    expect(r.frames.slice(-20).every((f) => !f.zooming && f.pose === 'open' && f.secondPose === 'open')).toBe(true);
    expect(r.events).toEqual([]);
    expect(r.sel).toEqual(ISOLATED);
  });

  it('the other hand comes in open (0.25 of the screen) and then pinches into a zoom: nothing', () => {
    for (const primary of ['pinch', 'open'] as const) {
      const frames = [
        ...repeat(16, (i) => [hand(primary, 0.45, 0.5), hand('open', 0.85 - i * 0.016, 0.5)]),
        ...repeat(10, () => pinchPair(0)),
      ];
      const r = drive(frames, ISOLATED);
      expect(r.frames.slice(0, 12).some((f) => f.secondPose === 'open'), primary).toBe(true);
      expect(r.events, primary).toEqual([]);
      expect(r.sel, primary).toEqual(ISOLATED);
    }
  });
});

describe('a deliberate sweep still works', () => {
  const rest = (cx: number, ms: number) => repeat(Math.round(ms / FRAME) + 1, () => [hand('open', 0.6, 0.5), hand('open', cx, 0.5)]);
  const move = (from: number, to: number, n: number) =>
    repeat(n, (i) => [hand('open', 0.6, 0.5), hand('open', from + ((to - from) * i) / (n - 1), 0.5)]);

  it('open palm, held still, swept: isolates in the hand\'s direction; rest and sweep back: restores', () => {
    const r = drive([
      ...rest(0.3, ARM_MS + 50),
      ...move(0.3, 0.3 - COMMIT - 0.07, 14),        // camera x falls: the hand goes right on screen
      ...rest(0.3 - COMMIT - 0.07, ARM_MS + 50),
      ...move(0.3 - COMMIT - 0.07, 0.3, 14),
    ], SELECTED);
    expect(r.events.map((e) => e.kind)).toEqual(['isolate', 'restore']);
    expect(r.events[0].dir.x).toBeGreaterThan(0.9);
    expect(r.sel).toEqual(SELECTED);
  });
});

describe('a sweep that runs out of the camera (v3.2, the user report)', () => {
  it('the other hand sweeps toward the screen edge and drops out of the frame: one isolate, that way', () => {
    const r = drive([
      ...repeat(Math.round((ARM_MS + 50) / FRAME) + 1, () => [hand('open', 0.6, 0.5), hand('open', 0.27, 0.5)]),
      // camera x 0.27 -> 0.17 is screen x ~0.72 -> ~0.82 (the palm centre, mirrored): 0.1 of travel,
      // under the commit distance, ending in the side's edge zone where the camera loses it
      ...repeat(10, (i) => [hand('open', 0.6, 0.5), hand('open', 0.27 - i * 0.0111, 0.5)]),
      ...repeat(4, () => [hand('open', 0.6, 0.5), hand('open', 0.17, 0.5)]), // landmark smoothing catches up
      ...repeat(10, () => [hand('open', 0.6, 0.5)]),          // the camera has lost it
    ], SELECTED);
    expect(r.frames.at(-1)!.second).toBeNull();
    expect(r.events.map((e) => e.kind)).toEqual(['isolate']);
    // and it is the exit that commits: on the first frame the camera has lost the hand
    const lost = r.frames.findIndex((f) => f.second === null);
    expect(r.sweeps.findIndex((s) => s.event)).toBe(lost);
    expect(r.events[0].dir.x).toBeGreaterThan(0.9);
    expect(r.sel).toEqual({ selected: 'liver', isolated: true });
  });

  it('a zoom whose hands spread out of the frame is still never a sweep', () => {
    const r = drive([
      ...repeat(20, (i) => pinchPair(i * 0.03)),                 // hands spread toward the edges
      ...repeat(10, () => [hand('pinch', 0.1, 0.5)]),             // one leaves the camera
    ], ISOLATED);
    expect(r.events).toEqual([]);
    expect(r.sel).toEqual(ISOLATED);
  });
});

describe('one thing at a time', () => {
  const armed = (primary: NormalizedLandmark[]) => repeat(Math.round((ARM_MS + 50) / FRAME), () => [primary, hand('open', 0.3, 0.5)]);

  it('an armed palm that curls into a point mid-sweep does not sweep (only an open palm sweeps)', () => {
    const r = drive([
      ...armed(hand('open', 0.6, 0.5)),
      ...repeat(16, (i) => [hand('open', 0.6, 0.5), hand('point', 0.3 - i * 0.017, 0.5)]),
    ], SELECTED);
    expect(r.frames.slice(-10).every((f) => f.secondPose === 'point')).toBe(true);
    expect(r.events).toEqual([]);
  });

  it('while the primary hand turns the model (pinch drag), the other hand does not sweep', () => {
    const r = drive([
      ...armed(hand('open', 0.6, 0.5)),
      ...repeat(16, (i) => [hand('pinch', 0.6 - i * 0.01, 0.5), hand('open', 0.3 - i * 0.017, 0.5)]),
    ], SELECTED);
    expect(r.frames.slice(-10).every((f) => f.pose === 'pinch' && f.secondPose === 'open')).toBe(true);
    expect(r.events).toEqual([]);
  });

  it('right after a snapshot the other hand cannot sweep for 0.8 s', () => {
    const r = drive([
      ...repeat(6, () => [hand('point', 0.5, 0.5), hand('point', 0.56, 0.5)]),       // the snapshot
      ...repeat(Math.round(500 / FRAME), () => [hand('open', 0.5, 0.5), hand('open', 0.3, 0.5)]), // still 0.5 s
      ...repeat(14, (i) => [hand('open', 0.5, 0.5), hand('open', 0.3 - i * 0.02, 0.5)]),
    ], SELECTED);
    expect(r.frames.some((f) => f.snapshot)).toBe(true);
    expect(r.events).toEqual([]);
  });
});

describe('the snapshot pose is only the snapshot', () => {
  it('held for 3 s over another part: no selection change and no sweep', () => {
    const r = drive(repeat(Math.round(3000 / FRAME), () => [hand('point', 0.5, 0.5), hand('point', 0.56, 0.5)]), SELECTED, 'heart');
    expect(r.frames.filter((f) => f.snapshot).length).toBe(1);
    expect(r.sel).toEqual(SELECTED);
    expect(r.events).toEqual([]);
  });
});

describe('v3.3: a sweep works however the hands are held (the user report: "not working at all")', () => {
  // the open palm rests (arms), then sweeps 0.18 to the right on screen (camera x falls)
  const sweepFrames = (palm: (cx: number) => NormalizedLandmark[], steering: NormalizedLandmark[] | null) => [
    ...repeat(Math.round((ARM_MS + 100) / FRAME), () => (steering ? [steering, palm(0.3)] : [palm(0.3)])),
    ...repeat(12, (i) => (steering ? [steering, palm(0.3 - i * 0.015)] : [palm(0.3 - i * 0.015)])),
  ];
  const open = (cx: number) => hand('open', cx, 0.5);
  const cases: [string, NormalizedLandmark[][][], (i: number) => string[]][] = [
    ['A. both hands, labelled as expected', sweepFrames(open, hand('point', 0.7, 0.5)), () => ['Right', 'Left']],
    ['B. only the sweeping hand in view', sweepFrames(open, null), () => ['Left']],
    ['C. MediaPipe swaps Left and Right', sweepFrames(open, hand('point', 0.7, 0.5)), () => ['Left', 'Right']],
    ['D. the other hand relaxed and curled', sweepFrames(open, hand('fist', 0.7, 0.5)), () => ['Right', 'Left']],
    ['E. the palm tilted 60 degrees', sweepFrames((cx) => rotate(open(cx), 60), hand('point', 0.7, 0.5)), () => ['Right', 'Left']],
    ['F. the palm sideways, wiping', sweepFrames((cx) => rotate(open(cx), 90), hand('point', 0.7, 0.5)), () => ['Right', 'Left']],
  ];

  for (const [name, frames, labels] of cases) {
    it(`${name}: one isolate, to the right`, () => {
      const r = drive(frames, SELECTED, null, labels);
      expect(r.events.map((e) => e.kind), name).toEqual(['isolate']);
      expect(r.events[0].dir.x, name).toBeGreaterThan(0.9);
    });
  }

  it('the same sweeps with nothing selected do nothing', () => {
    for (const [name, frames, labels] of cases) {
      expect(drive(frames, { selected: null, isolated: false }, null, labels).events, name).toEqual([]);
    }
  });

  it('a sweeping hand that closes does not hand its sweep to the other open palm', () => {
    const r = drive([
      ...repeat(Math.round((ARM_MS + 100) / FRAME), () => [hand('open', 0.7, 0.5), hand('open', 0.3, 0.5)]),
      ...repeat(4, (i) => [hand('open', 0.7, 0.5), hand('open', 0.3 - i * 0.015, 0.5)]),
      ...repeat(20, () => [hand('open', 0.7, 0.5), hand('fist', 0.24, 0.5)]),   // it closes; the other rests
    ], SELECTED);
    expect(r.events).toEqual([]);
  });
});

describe('a crowd: the operator lock in front of the engine (the user report)', () => {
  /** frames of (hand, label) pairs, filtered by the lock, then the engines, as HandTracker wires it */
  function crowdDrive(frames: [NormalizedLandmark[], string][][], start: Selection) {
    let lock = initialLock();
    let engine = initialEngine();
    let hs = { dwell: initialDwell(), sweep: initialSweep(), sel: start };
    const events: { kind: string; dir: { x: number } }[] = [];
    frames.forEach((all, i) => {
      const l = lockStep(lock, all.map(([lms, label]) => ({ lms, label })), i * FRAME);
      lock = l.state;
      const kept = l.keep.map((k) => all[k]);
      const r = step(engine, kept.length ? kept.map(([lms]) => lms) : null, kept.map(([, label]) => label), i * FRAME);
      engine = r.state;
      const t = selectionTick(hs, r.frame, null, i * FRAME);
      hs = t.state;
      if (t.sweep.event) events.push(t.sweep.event);
    });
    return { sel: hs.sel, events };
  }
  const me = (pose: 'open' | 'point', cx: number, label: string): [NormalizedLandmark[], string] => [scale(hand(pose, cx, 0.5), 1.3), label];
  // a smaller (further back) stranger waving an open palm left and right, far across the frame
  const stranger = (i: number): [NormalizedLandmark[], string] => [scale(hand('open', 0.12 + 0.08 * Math.sin(i / 3), 0.3), 0.6), 'Left'];
  const third = (): [NormalizedLandmark[], string] => [scale(hand('fist', 0.9, 0.25), 0.55), 'Right'];

  it("the operator sweeps while a stranger waves nearby: one isolate, the operator's, to the right", () => {
    const r = crowdDrive([
      ...repeat(10, () => [me('point', 0.7, 'Right'), me('open', 0.3, 'Left')]),                       // the operator, alone
      ...repeat(Math.round((ARM_MS + 100) / FRAME), (i) => [me('point', 0.7, 'Right'), me('open', 0.3, 'Left'), stranger(i), third()]),
      ...repeat(12, (i) => [me('point', 0.7, 'Right'), me('open', 0.3 - i * 0.015, 'Left'), stranger(i + 40), third()]),
    ], SELECTED);
    expect(r.events.map((e) => e.kind)).toEqual(['isolate']);
    expect(r.events[0].dir.x).toBeGreaterThan(0.9);
  });

  it("without the lock, a stranger's resting palm arms a sweep on the operator's model (why the lock exists)", () => {
    let engine = initialEngine();
    let hs = { dwell: initialDwell(), sweep: initialSweep(), sel: SELECTED };
    const events: string[] = [];
    // MediaPipe's two slots taken by the stranger (resting an open palm in view) and the third
    // person, as with numHands: 2 and no lock: the stranger's palm arms a sweep on the operator's model
    const resting = (): [NormalizedLandmark[], string] => [scale(hand('open', 0.12, 0.3), 0.6), 'Left'];
    repeat(60, () => [resting(), third()]).forEach((all, i) => {
      const r = step(engine, all.map(([lms]) => lms), all.map(([, l]) => l), i * FRAME);
      engine = r.state;
      const t = selectionTick(hs, r.frame, null, i * FRAME);
      hs = t.state;
      if (t.sweep.armed) events.push('stranger armed a sweep');
    });
    expect(events.length).toBeGreaterThan(0);
  });
});


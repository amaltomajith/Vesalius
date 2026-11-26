import { describe, expect, it } from 'vitest';
import { initialLock, lockStep, LOST_MS, REACH, type DetectedHand } from './operatorLock';
import { hand, repeat, scale } from './testHands';
import type { NormalizedLandmark } from './gestureEngine';

const FRAME = 16;
// a named hand at camera position (cx, cy), `k` times the reference size (nearer = larger), with
// MediaPipe's label
type Who = { id: string; lms: NormalizedLandmark[]; label: string };
const h = (id: string, label: 'Left' | 'Right', cx: number, cy = 0.5, k = 1): Who =>
  ({ id, label, lms: scale(hand('open', cx, cy), k) });

/** Run the lock; per frame, the ids it passed on. */
function follow(frames: Who[][]) {
  let st = initialLock();
  return frames.map((hs, i) => {
    const r = lockStep(st, hs.map((x): DetectedHand => ({ lms: x.lms, label: x.label })), i * FRAME);
    st = r.state;
    return r.keep.map((k) => hs[k].id).sort();
  });
}
const R = (cx = 0.6, k = 1.2) => h('R', 'Right', cx, 0.5, k);
const L = (cx = 0.4, k = 1.2) => h('L', 'Left', cx, 0.5, k);

describe('two hands or fewer: exactly as before', () => {
  it('every hand passes, on every frame, however they are held', () => {
    const cases: Who[][][] = [
      repeat(20, () => [R()]),
      repeat(20, (i) => [R(0.6 - i * 0.01), L(0.35 + i * 0.01)]),
      repeat(20, () => [R(0.6, 2.2), L(0.3, 1)]),          // one hand pushed toward the camera
      repeat(20, () => [R(0.95, 1), L(0.05, 1)]),          // arms spread to the edges
      repeat(20, () => [R(), h('x', 'Right', 0.1, 0.2, 0.4)]),
    ];
    for (const frames of cases) expect(follow(frames).every((k, i) => k.length === frames[i].length)).toBe(true);
  });
});

describe('a crowd (more than two hands): only the operator', () => {
  it("the nearest person's hands are followed; smaller (further) strangers are ignored", () => {
    const k = follow(repeat(10, () => [h('s1', 'Right', 0.15, 0.4, 0.5), R(), L(), h('s2', 'Left', 0.85, 0.3, 0.6)]));
    expect(k.at(-1)).toEqual(['L', 'R']);
  });

  it('the operator, tracked while alone, keeps control when others come into view', () => {
    const k = follow([
      ...repeat(10, () => [R(0.6, 1), L(0.4, 1)]),                                      // alone
      ...repeat(10, () => [R(0.6, 1), L(0.4, 1), h('big', 'Right', 0.1, 0.3, 1.6)]),   // a nearer stranger arrives
    ]);
    expect(k.slice(10).every((x) => x.join() === 'L,R')).toBe(true);
  });

  it('following beats size: a stranger walking close past the camera does not take over', () => {
    const k = follow([
      ...repeat(10, () => [R(0.6, 1), L(0.4, 1)]),
      ...repeat(20, (i) => [R(0.6, 1), L(0.4, 1), h('passer', 'Left', 0.05 + i * 0.01, 0.2, 1.9)]),
    ]);
    expect(k.slice(10).every((x) => x.join() === 'L,R')).toBe(true);
  });

  it("with one operator hand up, a stranger's hand is not taken as its other hand", () => {
    const k = follow([
      ...repeat(10, () => [R(0.6, 1.2)]),
      ...repeat(10, () => [
        R(0.6, 1.2),
        h('same-side', 'Right', 0.4, 0.5, 1.2),      // the same label as the operator's hand
        h('tiny', 'Left', 0.45, 0.4, 0.4),           // much further away
        h('far', 'Left', 0.6 + (REACH + 2) * 0.13, 0.5, 1.2),
      ]),
    ]);
    expect(k.slice(10).every((x) => x.join() === 'R')).toBe(true);
  });

  it("the operator's other hand, raised in a crowd, joins (even with arms spread wide)", () => {
    const k = follow([
      ...repeat(10, () => [R(0.9, 1), h('s', 'Right', 0.3, 0.3, 0.5), h('s2', 'Left', 0.5, 0.2, 0.5)]),
      ...repeat(5, () => [R(0.9, 1), h('s', 'Right', 0.3, 0.3, 0.5), h('s2', 'Left', 0.5, 0.2, 0.5), L(0.08, 1)]),
    ]);
    expect(k.at(-1)).toEqual(['L', 'R']);
  });
});

describe('dropouts and jumps, in a crowd', () => {
  // s1 the nearest of them, s2 its other hand, s3 someone further back
  const crowd = () => [h('s1', 'Right', 0.2, 0.3, 0.7), h('s2', 'Left', 0.4, 0.3, 0.6), h('s3', 'Right', 0.85, 0.2, 0.4)];

  it("the operator's hands missing for 300 ms keep the lock: nobody else is followed meanwhile", () => {
    const gap = Math.round(300 / FRAME);
    const k = follow([
      ...repeat(10, () => [R(), L(), ...crowd()]),
      ...repeat(gap, () => crowd()),
      ...repeat(5, () => [R(0.62), L(0.42), ...crowd()]),
    ]);
    expect(k.slice(10, 10 + gap).every((x) => x.length === 0)).toBe(true);
    expect(k.at(-1)).toEqual(['L', 'R']);
  });

  it('gone for longer than LOST_MS: the nearest person present is picked', () => {
    const gone = Math.round((LOST_MS + 100) / FRAME);
    const k = follow([...repeat(10, () => [R(), L(), ...crowd()]), ...repeat(gone, () => crowd())]);
    expect(k.at(-1)).toEqual(['s1', 's2']);   // the nearest (largest) and their other hand
  });

  it('a detection 0.3 of the frame away from a tracked hand is not that hand', () => {
    const k = follow([...repeat(10, () => [R(0.6, 1), ...crowd()]), [h('jump', 'Right', 0.9, 0.5, 1), ...crowd()]]);
    expect(k.at(-1)).toEqual([]);
  });
});

describe('the lock, in plain numbers', () => {
  // three hands, so the crowd rules apply (two or fewer pass through by design): s1 and s2 one
  // person (the nearer of them), s3 someone further back
  const crowd = () => [h('s1', 'Right', 0.15, 0.2, 0.45), h('s2', 'Left', 0.75, 0.2, 0.45), h('s3', 'Right', 0.5, 0.1, 0.25)];

  it('unseen for a second, a tracked hand is not taken by a same-size hand 0.5 away', () => {
    const k = follow([
      ...repeat(10, () => [R(0.3, 1), ...crowd()]),
      ...repeat(Math.round(1000 / FRAME), () => crowd()),
      [h('elsewhere', 'Right', 0.8, 0.5, 1), ...crowd()],
    ]);
    expect(k.at(-1)).toEqual([]);
  });

  it('someone far from the camera: their other hand is within 16 palm lengths, not beyond', () => {
    // k = 0.4: a palm of about 0.042 of the frame, so an arm span of about 0.67
    const far = (cx: number) => repeat(10, () => [h('R', 'Right', 0.1, 0.5, 0.4), h('L', 'Left', cx, 0.5, 0.4), h('s', 'Right', 0.5, 0.15, 0.2)]);
    expect(follow(far(0.6)).at(-1)).toEqual(['L', 'R']);
    expect(follow(far(0.95)).at(-1)).toEqual(['R']);
  });

  it('gone 1.6 s: the nearest person present is picked; gone 1.3 s: still nobody else', () => {
    const run = (ms: number) => follow([...repeat(10, () => [R(), L(), ...crowd()]), ...repeat(Math.round(ms / FRAME), () => crowd())]).at(-1);
    expect(run(1600)).toEqual(['s1', 's2']);
    expect(run(1300)).toEqual([]);
  });
});


import { describe, expect, it } from 'vitest';
import {
  ARM_MS, COMMIT, DWELL_MS, EDGE, EXIT_MIN, dwellStep, initialDwell, initialSweep, resolveDwell, sweepStep,
  type DwellState, type SweepFrame, type SweepState,
} from './selectionEngine';
import type { Pt } from './gestureEngine';

const FRAME = 16;

/** Hold frames: each item is [pointing, target], 16 ms apart; returns progress and fires. */
function hold(seq: [boolean, string | null][], start: DwellState = initialDwell()) {
  let st = start;
  const fires: (string | null)[] = [];
  let progress = 0;
  seq.forEach(([pointing, target], i) => {
    const r = dwellStep(st, { pointing, target }, i * FRAME);
    st = r.state;
    progress = r.progress;
    if (r.fire) fires.push(r.fire.target);
  });
  return { st, fires, progress };
}
const frames = (ms: number) => Math.round(ms / FRAME) + 1;
const repeat = <T,>(n: number, f: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => f(i));

describe('point and hold', () => {
  it('selects after holding 2 s on a part, not before', () => {
    expect(hold(repeat(frames(DWELL_MS - 100), () => [true, 'liver'])).fires).toEqual([]);
    expect(hold(repeat(frames(DWELL_MS), () => [true, 'liver'])).fires).toEqual(['liver']);
  });

  it('the hold is the 2 s the user chose: not at 1.9 s, at 2 s', () => {
    expect(hold(repeat(frames(1900), () => [true, 'liver'])).fires).toEqual([]);
    expect(hold(repeat(frames(2000), () => [true, 'liver'])).fires).toEqual(['liver']);
  });

  it('fills a progress ring on the way', () => {
    const r = hold(repeat(frames(DWELL_MS / 2), () => [true, 'liver']));
    expect(r.progress).toBeGreaterThan(0.45);
    expect(r.progress).toBeLessThan(0.55);
  });

  it('never selects without pointing (an open palm rests)', () => {
    expect(hold(repeat(frames(3000), () => [false, 'liver'])).fires).toEqual([]);
  });

  it('starts over when the cursor moves to another part', () => {
    const r = hold([...repeat(frames(1000), () => [true, 'liver'] as [boolean, string]), ...repeat(frames(1000), () => [true, 'heart'] as [boolean, string])]);
    expect(r.fires).toEqual([]);
  });

  it('survives a brief flicker off the part at its edge', () => {
    const r = hold([
      ...repeat(frames(DWELL_MS / 2 + 50), () => [true, 'liver'] as [boolean, string]),
      ...repeat(6, () => [true, null] as [boolean, null]), // ~100 ms off the edge
      ...repeat(frames(DWELL_MS / 2 + 50), () => [true, 'liver'] as [boolean, string]),
    ]);
    expect(r.fires).toEqual(['liver']);
  });

  it('fires once per hold', () => {
    expect(hold(repeat(frames(5000), () => [true, 'liver'])).fires).toEqual(['liver']);
  });

  it('holding on empty space fires with no target (step back)', () => {
    expect(hold(repeat(frames(DWELL_MS), () => [true, null])).fires).toEqual([null]);
  });
});

describe('resolveDwell', () => {
  const none = { selected: null, isolated: false };
  const sel = { selected: 'liver', isolated: false };
  const iso = { selected: 'liver', isolated: true };
  it('a part selects it; holding on the selection changes nothing', () => {
    expect(resolveDwell(none, 'liver')).toEqual(sel);
    expect(resolveDwell(sel, 'heart')).toEqual({ selected: 'heart', isolated: false });
    expect(resolveDwell(sel, 'liver')).toEqual(sel);
    expect(resolveDwell(iso, 'liver')).toEqual(iso);
  });
  it('empty space steps back one level', () => {
    expect(resolveDwell(iso, null)).toEqual(sel);
    expect(resolveDwell(sel, null)).toEqual(none);
    expect(resolveDwell(none, null)).toEqual(none);
  });
});

/** One frame of the other hand: a palm position (an open palm), null (lowered), or the full input. */
type HandStep = Pt | null | { at: Pt | null; open?: boolean; blocked?: boolean };

/** Drive the other hand along `path`, 16 ms apart. */
function sweep(path: HandStep[], opts: { active?: boolean; isolated?: boolean } = {}, start: SweepState = initialSweep()) {
  let st = start;
  const out: SweepFrame[] = [];
  path.forEach((step, i) => {
    const h = step && 'at' in step ? step : { at: step };
    const r = sweepStep(st, {
      second: h.at, secondOpen: h.open ?? true, blocked: h.blocked ?? false,
      active: opts.active ?? true, isolated: opts.isolated ?? false,
    }, i * FRAME);
    st = r.state;
    out.push(r);
  });
  return { st, out, events: out.filter((f) => f.event).map((f) => f.event!) };
}
const line = (from: Pt, to: Pt, n: number): Pt[] =>
  repeat(n, (i) => ({ x: from.x + ((to.x - from.x) * i) / (n - 1), y: from.y + ((to.y - from.y) * i) / (n - 1) }));
/** an open palm held still long enough to arm */
const arm = (at: Pt): Pt[] => repeat(frames(ARM_MS), () => at);

describe('arming the other hand (v3.1: a sweep has to be meant)', () => {
  it('an open palm held still for 0.4 s arms; not at 0.3 s', () => {
    const at = { x: 0.3, y: 0.5 };
    expect(sweep(repeat(frames(300), () => at)).out.at(-1)!.armed).toBe(false);
    expect(sweep(repeat(frames(400), () => at)).out.at(-1)!.armed).toBe(true);
  });

  it('a hand that arrives moving does nothing, however far it goes', () => {
    const r = sweep(line({ x: 0.1, y: 0.5 }, { x: 0.1 + COMMIT * 2, y: 0.5 }, 20));
    expect(r.events).toEqual([]);
    expect(r.out.every((f) => !f.live)).toBe(true);
  });

  it('a hand that stops being an open palm mid-sweep cancels it: spring back, no event, re-arm needed', () => {
    const at = { x: 0.3, y: 0.5 };
    const r = sweep([
      ...arm(at),
      ...line(at, { x: 0.4, y: 0.5 }, 6),
      { at: { x: 0.42, y: 0.5 }, open: false },                  // starts to pinch
      ...line({ x: 0.42, y: 0.5 }, { x: 0.42 + COMMIT, y: 0.5 }, 10), // open again, still moving
    ]);
    expect(r.events).toEqual([]);
    expect(r.out[frames(ARM_MS) + 6].offset).toEqual({ x: 0, y: 0 });
    expect(r.out.slice(-10).every((f) => !f.live)).toBe(true);
  });

  it('blocked (zoom, grab, snapshot) means no sweep, then none for 0.8 s plus a fresh 0.4 s rest', () => {
    const at = { x: 0.3, y: 0.5 };
    const blocked = repeat(5, () => ({ at, blocked: true }));
    const r = sweep([...arm(at), ...line(at, { x: 0.35, y: 0.5 }, 3), ...blocked, ...repeat(frames(1300), () => at)]);
    const end = frames(ARM_MS) + 3 + 5;                       // first frame after the block
    expect(r.out[end - 1].live).toBe(false);
    expect(r.out[end + Math.round(1100 / FRAME)].armed).toBe(false); // 1.1 s after: not yet
    expect(r.out[end + Math.round(1250 / FRAME)].armed).toBe(true);  // 0.8 s + 0.4 s
  });
});

describe('sweeping the other hand', () => {
  it('the parts follow the hand live, before the commit distance', () => {
    const r = sweep([...arm({ x: 0.3, y: 0.5 }), ...line({ x: 0.3, y: 0.5 }, { x: 0.4, y: 0.5 }, 10)]);
    const last = r.out.at(-1)!;
    expect(last.live).toBe(true);
    // measured from where the palm came to rest and armed
    expect(last.offset.x).toBeCloseTo(0.1, 9);
    expect(last.offset.y).toBeCloseTo(0, 9);
    expect(r.events).toEqual([]);
  });

  it('sweeping far enough clears the rest, in the direction of the sweep', () => {
    const go = (from: Pt, to: Pt) => sweep([...arm(from), ...line(from, to, 12)]);
    const right = go({ x: 0.3, y: 0.5 }, { x: 0.3 + COMMIT + 0.05, y: 0.5 });
    expect(right.events.map((e) => e.kind)).toEqual(['isolate']);
    expect(right.events[0].dir.x).toBeCloseTo(1, 5);
    const left = go({ x: 0.7, y: 0.5 }, { x: 0.7 - COMMIT - 0.05, y: 0.5 });
    expect(left.events[0].dir.x).toBeCloseTo(-1, 5);
    const diag = go({ x: 0.3, y: 0.3 }, { x: 0.5, y: 0.5 });
    expect(diag.events[0].dir.x).toBeCloseTo(Math.SQRT1_2, 3);
    expect(diag.events[0].dir.y).toBeCloseTo(Math.SQRT1_2, 3);
  });

  it('a pause mid-sweep keeps the parts out; a 0.4 s rest springs them back', () => {
    const at = { x: 0.3, y: 0.5 };
    const go = [...arm(at), ...line(at, { x: 0.4, y: 0.5 }, 8)];
    const pause = (ms: number) => repeat(Math.round(ms / FRAME), () => ({ x: 0.4, y: 0.5 }));
    expect(sweep([...go, ...pause(300)]).out.at(-1)!.offset.x).toBeCloseTo(0.1, 9);
    const back = sweep([...go, ...pause(450)]);
    expect(back.out.at(-1)!.offset).toEqual({ x: 0, y: 0 });
    expect(back.events).toEqual([]);
  });

  it('a resting hand that trembles a little still counts as resting', () => {
    const at = { x: 0.3, y: 0.5 };
    const tremble = repeat(Math.round(450 / FRAME), (i) => ({ x: 0.4 + (i % 2 ? 0.006 : -0.006), y: 0.5 }));
    expect(sweep([...arm(at), ...line(at, { x: 0.4, y: 0.5 }, 8), ...tremble]).out.at(-1)!.offset).toEqual({ x: 0, y: 0 });
  });

  it('lowering the hand early springs back with no event', () => {
    const at = { x: 0.3, y: 0.5 };
    const r = sweep([...arm(at), ...line(at, { x: 0.4, y: 0.5 }, 8), null]);
    expect(r.events).toEqual([]);
    expect(r.out.at(-1)!.offset).toEqual({ x: 0, y: 0 });
  });

  it('one long sweep clears once: the rest of the motion does not restore', () => {
    const at = { x: 0.1, y: 0.5 };
    const r = sweep([...arm(at), ...line(at, { x: 0.1 + COMMIT * 3, y: 0.5 }, 40)]);
    expect(r.events.map((e) => e.kind)).toEqual(['isolate']);
  });

  it('when cleared, a new sweep (after the hand rests) brings everything back', () => {
    const at = { x: 0.5, y: 0.5 };
    const r = sweep([...arm(at), ...line(at, { x: 0.5 - COMMIT - 0.05, y: 0.5 }, 12)], { isolated: true });
    expect(r.events.map((e) => e.kind)).toEqual(['restore']);
  });

  it('with nothing selected a sweep does nothing', () => {
    const at = { x: 0.2, y: 0.5 };
    const r = sweep([...arm(at), ...line(at, { x: 0.8, y: 0.5 }, 20)], { active: false });
    expect(r.events).toEqual([]);
    expect(r.out.every((f) => !f.live && !f.armed)).toBe(true);
  });
});

describe('v3.2: a shorter sweep, and leaving the camera through the side commits', () => {
  // the palm arms at `from`, sweeps to `to`, then the camera loses it (null)
  const thenLost = (from: Pt, to: Pt, opts: { isolated?: boolean } = {}) =>
    sweep([...arm(from), ...line(from, to, 8), null], opts);

  it('0.12 of the screen clears; 0.10 does not', () => {
    const go = (dx: number) => sweep([...arm({ x: 0.4, y: 0.5 }), ...line({ x: 0.4, y: 0.5 }, { x: 0.4 + dx, y: 0.5 }, 10)]);
    expect(go(0.125).events.map((e) => e.kind)).toEqual(['isolate']);
    expect(go(0.1).events).toEqual([]);
  });

  it('a hand that sweeps right and leaves through the right side clears the rest, to the right', () => {
    const r = thenLost({ x: 0.83, y: 0.5 }, { x: 0.9, y: 0.5 });
    expect(r.events.map((e) => e.kind)).toEqual(['isolate']);
    expect(r.events[0].dir.x).toBeCloseTo(1, 5);
  });

  it('the same through the left side, to the left; when cleared, a side exit brings it back', () => {
    const left = thenLost({ x: 0.17, y: 0.5 }, { x: 0.1, y: 0.5 });
    expect(left.events[0].dir.x).toBeCloseTo(-1, 5);
    expect(thenLost({ x: 0.17, y: 0.5 }, { x: 0.1, y: 0.5 }, { isolated: true }).events.map((e) => e.kind)).toEqual(['restore']);
  });

  it('no false sweeps: too short, lowered, lost mid-frame, lost before arming, or heading the other way', () => {
    expect(thenLost({ x: 0.87, y: 0.5 }, { x: 0.9, y: 0.5 }).events).toEqual([]);              // only 0.03
    expect(thenLost({ x: 0.85, y: 0.6 }, { x: 0.88, y: 0.75 }).events).toEqual([]);            // mostly down: lowered
    expect(thenLost({ x: 0.45, y: 0.5 }, { x: 0.53, y: 0.5 }).events).toEqual([]);             // lost mid-frame
    expect(thenLost({ x: 0.2, y: 0.5 }, { x: 0.12, y: 0.5 }).events.length).toBe(1);           // (control: left edge)
    expect(sweep([...line({ x: 0.8, y: 0.5 }, { x: 0.9, y: 0.5 }, 6), null]).events).toEqual([]); // never armed
    expect(thenLost({ x: 0.9, y: 0.5 }, { x: 0.83, y: 0.5 }).events).toEqual([]);              // moving away from the near edge
  });

  it('only sideways travel clears: lowering an armed palm, however far, never does', () => {
    const r = sweep([...arm({ x: 0.7, y: 0.4 }), ...line({ x: 0.7, y: 0.4 }, { x: 0.72, y: 0.8 }, 16)]);
    expect(r.out.some((f) => f.live)).toBe(true);        // the parts follow it while it moves
    expect(r.events).toEqual([]);
    expect(sweep([...arm({ x: 0.7, y: 0.4 }), ...line({ x: 0.7, y: 0.4 }, { x: 0.72, y: 0.8 }, 16), null]).events).toEqual([]);
  });

  it('the exit thresholds are what they say: EXIT_MIN of travel, within EDGE of the side', () => {
    const at = 1 - EDGE + 0.01;
    expect(thenLost({ x: at - EXIT_MIN - 0.005, y: 0.5 }, { x: at, y: 0.5 }).events.length).toBe(1);
    expect(thenLost({ x: at - EXIT_MIN + 0.01, y: 0.5 }, { x: at, y: 0.5 }).events).toEqual([]);
    const inside = 1 - EDGE - 0.02;
    expect(thenLost({ x: inside - 0.08, y: 0.5 }, { x: inside, y: 0.5 }).events).toEqual([]);
  });

  it('in plain numbers: lost at 0.82 of the screen is the edge; lost at 0.7 is not', () => {
    expect(thenLost({ x: 0.74, y: 0.5 }, { x: 0.82, y: 0.5 }).events.length).toBe(1);
    expect(thenLost({ x: 0.62, y: 0.5 }, { x: 0.7, y: 0.5 }).events).toEqual([]);
  });
});

describe('v3.3: a real hand held still sways', () => {
  it('a palm swaying 1.1% either way still arms in 0.4 s', () => {
    const sway = repeat(frames(600), (i) => ({ x: 0.4 + (i % 4 < 2 ? 0.011 : -0.011), y: 0.5 + (i % 3 === 0 ? 0.006 : 0) }));
    expect(sweep(sway).out.at(-1)!.armed).toBe(true);
  });

  it('the arming ring fills while it holds still, and is full when armed', () => {
    const r = sweep(repeat(frames(500), () => ({ x: 0.4, y: 0.5 })));
    expect(r.out[frames(200)].arming).toBeGreaterThan(0.4);
    expect(r.out[frames(200)].arming).toBeLessThan(0.6);
    expect(r.out.at(-1)!.arming).toBe(1);
  });
});


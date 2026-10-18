import { describe, expect, it } from 'vitest';
import { classifyPose, openPalm, pinching, resolveClick, PINCH_OFF, PINCH_ON, SNAP_DX, SNAP_DY } from './gestureEngine';
import { hand, repeat, rotate, run } from './testHands';

describe('poses', () => {
  it('recognises open palm, point, pinch and fist', () => {
    for (const pose of ['open', 'point', 'pinch', 'fist'] as const) {
      const h = hand(pose);
      expect(classifyPose(h, pinching(h, false)), pose).toBe(pose);
    }
  });

  it('a closed hand is a fist even though its thumb touches the index finger', () => {
    const h = hand('fist');
    expect(pinching(h, false)).toBe(true);
    expect(classifyPose(h, true)).toBe('fist');
  });

  it('pinch has hysteresis: between the two thresholds it keeps its previous state', () => {
    const between = (PINCH_ON + PINCH_OFF) / 2;
    const h = hand('pinch', 0.5, 0.5, between);
    expect(pinching(h, false)).toBe(false);
    expect(pinching(h, true)).toBe(true);
  });
});

describe('the cursor and the other hand', () => {
  it('pointing shows a cursor at the index tip; a closed hand has none', () => {
    const r = run([...repeat(4, () => [hand('point')]), ...repeat(8, () => [hand('fist')])]);
    expect(r.frames[3].cursor).not.toBeNull();
    expect(r.frames[3].gesture).toBe('POINT');
    expect(r.frames.at(-1)!.cursor).toBeNull();
  });

  it("reports the other hand's palm for sweeps, while the right hand steers", () => {
    const r = run(
      repeat(6, () => [hand('point', 0.6, 0.5), hand('open', 0.3, 0.5)]),
      repeat(6, () => ['Right', 'Left']),
    );
    const f = r.frames.at(-1)!;
    expect(f.second).not.toBeNull();
    expect(f.second!.x).toBeGreaterThan(0.6); // mirrored: a hand at camera x 0.3 is on screen right
    expect(f.gesture).toBe('POINT');          // the right hand still drives the cursor
  });

  it('reports the other hand with its pose (the sweep decides); both pinching is a zoom', () => {
    const one = run(repeat(6, () => [hand('point', 0.6, 0.5), hand('pinch', 0.3, 0.5)]), repeat(6, () => ['Right', 'Left']));
    expect(one.frames.at(-1)!.second).not.toBeNull();
    expect(one.frames.at(-1)!.secondPose).toBe('pinch');
    expect(one.frames.at(-1)!.zooming).toBe(false);
    const open = run(repeat(6, () => [hand('point', 0.6, 0.5), hand('open', 0.3, 0.5)]), repeat(6, () => ['Right', 'Left']));
    expect(open.frames.at(-1)!.secondPose).toBe('open');
    const both = run(repeat(6, () => [hand('pinch', 0.6, 0.5), hand('pinch', 0.3, 0.5)]), repeat(6, () => ['Right', 'Left']));
    expect(both.frames.at(-1)!.gesture).toBe('ZOOM');
    expect(both.frames.at(-1)!.zooming).toBe(true);
    expect(both.frames.at(-1)!.second).toBeNull();
  });

  it('with only the left hand up, it steers (and there is no other hand)', () => {
    const r = run(repeat(6, () => [hand('point')]), repeat(6, () => ['Left']));
    expect(r.frames.at(-1)!.gesture).toBe('POINT');
    expect(r.frames.at(-1)!.second).toBeNull();
  });
});

describe('a test release really releases', () => {
  it('six pointing frames after a pinch end the pinch (else a release test proves nothing)', () => {
    const r = run([...repeat(8, () => [hand('pinch')]), ...repeat(6, () => [hand('point')])]);
    expect(r.frames.at(-1)!.pose).toBe('point');
  });
});

describe('dragging', () => {
  it('pinch and drag turns the model', () => {
    const r = run([
      ...repeat(4, () => [hand('point')]),
      ...repeat(20, (i) => [hand('pinch', 0.5 - i * 0.012, 0.5)]), // camera x falls = screen x rises
      ...repeat(6, () => [hand('point', 0.26, 0.5)]),
    ]);
    expect(r.state.rotY).toBeGreaterThan(0.2);
    expect(r.frames.some((f) => f.gesture === 'ROTATE')).toBe(true);
  });

  it('fist and drag moves the model', () => {
    const r = run(repeat(20, (i) => [hand('fist', 0.5 - i * 0.012, 0.5)]));
    expect(r.state.pos.x).toBeGreaterThan(0.5);
    expect(r.state.rotY).toBe(0);
    expect(r.frames.at(-1)!.gesture).toBe('PAN');
  });

  it('an open palm rests: moving it changes nothing', () => {
    const r = run(repeat(20, (i) => [hand('open', 0.3 + i * 0.02, 0.5 - i * 0.01)]));
    expect(r.state.pos).toEqual({ x: 0, y: 0 });
    expect(r.state.rotY).toBe(0);
    expect(r.frames.at(-1)!.gesture).toBe('REST');
  });

  it('both hands pinching zooms; spreading them makes the model bigger', () => {
    const r = run(
      repeat(20, (i) => [hand('pinch', 0.4 - i * 0.008, 0.5), hand('pinch', 0.6 + i * 0.008, 0.5)]),
      repeat(20, () => ['Right', 'Left']),
    );
    expect(r.frames.at(-1)!.gesture).toBe('ZOOM');
    expect(r.state.scale).toBeGreaterThan(1.2);
  });
});

describe('resolveClick: one level at a time', () => {
  const none = { selected: null, isolated: false };
  const sel = { selected: 'liver', isolated: false };
  const iso = { selected: 'liver', isolated: true };

  it('nothing selected: a part selects it, empty space does nothing', () => {
    expect(resolveClick(none, 'liver')).toEqual(sel);
    expect(resolveClick(none, null)).toEqual(none);
  });
  it('a part selected: the same part isolates, another selects that, empty space deselects', () => {
    expect(resolveClick(sel, 'liver')).toEqual(iso);
    expect(resolveClick(sel, 'heart')).toEqual({ selected: 'heart', isolated: false });
    expect(resolveClick(sel, null)).toEqual(none);
  });
  it('isolated: the same part stays, another shows all with it selected, empty space shows all', () => {
    expect(resolveClick(iso, 'liver')).toEqual(iso);
    expect(resolveClick(iso, 'heart')).toEqual({ selected: 'heart', isolated: false });
    expect(resolveClick(iso, null)).toEqual(sel);
  });
});

describe('the AI snapshot', () => {
  const lr = (n: number) => repeat(n, () => ['Right', 'Left']);
  const touching = () => [hand('point', 0.5, 0.5), hand('point', 0.56, 0.5)];  // index tips 0.06 apart
  const apart = () => [hand('point', 0.5, 0.5), hand('point', 0.8, 0.5)];

  it('both index fingers pointing at each other fire it once per touch', () => {
    const r = run([...repeat(10, touching), ...repeat(4, apart), ...repeat(6, touching)], lr(20));
    expect(r.frames.filter((f) => f.snapshot).length).toBe(2);
    expect(r.frames[0].snapshot).toBe(true);
    expect(r.frames[5].snapPose).toBe(true);     // held: the pose stays on, the shot does not repeat
    expect(r.frames[5].snapshot).toBe(false);
    expect(r.frames[12].snapPose).toBe(false);
  });

  it('only with two points: a point and a pinch, or fingers far apart, are not a snapshot', () => {
    const pinch = run(repeat(8, () => [hand('point', 0.5, 0.5), hand('pinch', 0.56, 0.5)]), lr(8));
    expect(pinch.frames.some((f) => f.snapPose || f.snapshot)).toBe(false);
    const far = run(repeat(8, () => [hand('point', 0.5, 0.5), hand('point', 0.5 + SNAP_DX + 0.02, 0.5)]), lr(8));
    expect(far.frames.some((f) => f.snapshot)).toBe(false);
    const high = run(repeat(8, () => [hand('point', 0.5, 0.5), hand('point', 0.52, 0.5 + SNAP_DY + 0.02)]), lr(8));
    expect(high.frames.some((f) => f.snapshot)).toBe(false);
  });
});

describe('the sweeping hand (v3.3)', () => {
  it('an open palm is open at any angle; a fist, a point or a pinch never is', () => {
    for (const deg of [0, 45, 60, 90, 135, 180, -90]) expect(openPalm(rotate(hand('open'), deg), false), `${deg}`).toBe(true);
    expect(openPalm(hand('fist'), false)).toBe(false);
    expect(openPalm(hand('point'), false)).toBe(false);
    expect(openPalm(rotate(hand('point'), 90), false)).toBe(false);
    expect(openPalm(hand('pinch'), true)).toBe(false);
  });

  it('a relaxed open hand with its little finger curled is still open (3 of 4 fingers)', () => {
    const relaxed = hand('open').map((p, i) => (i >= 18 ? hand('fist')[i] : p));
    expect(openPalm(relaxed, false)).toBe(true);
    expect(openPalm(rotate(relaxed, 90), false)).toBe(true);
    const twoDown = relaxed.map((p, i) => (i >= 14 && i <= 16 ? hand('fist')[i] : p));
    expect(openPalm(twoDown, false)).toBe(false);
  });

  it('a pinching hand is never an open palm, even with three fingers up', () => {
    expect(openPalm(hand('pinch'), false)).toBe(true);    // the shape alone: three fingers up
    expect(openPalm(hand('pinch'), true)).toBe(false);    // but it is pinching
  });

  it('picks the open palm whatever its label; with two open palms, the one not steering', () => {
    const one = run(repeat(4, () => [hand('point', 0.7, 0.5), hand('open', 0.3, 0.5)]), repeat(4, () => ['Left', 'Right']));
    expect(one.frames.at(-1)!.sweep!.at.x).toBeGreaterThan(0.6);       // the open hand, labelled the "steering" side
    const two = run(repeat(4, () => [hand('open', 0.7, 0.5), hand('open', 0.3, 0.5)]), repeat(4, () => ['Right', 'Left']));
    expect(two.frames.at(-1)!.sweep!.at.x).toBeGreaterThan(0.6);       // the non-steering (Left) one, on screen right
    const alone = run(repeat(4, () => [hand('open', 0.4, 0.5)]), repeat(4, () => ['Right']));
    expect(alone.frames.at(-1)!.sweep).not.toBeNull();                  // a lone open hand can sweep
    const none = run(repeat(4, () => [hand('point', 0.4, 0.5)]), repeat(4, () => ['Right']));
    expect(none.frames.at(-1)!.sweep).toBeNull();
  });

  it('the sweeping palm keeps the role while it moves, and reports the frame it closes', () => {
    const r = run([
      ...repeat(4, () => [hand('point', 0.7, 0.5), hand('open', 0.3, 0.5)]),
      ...repeat(6, (i) => [hand('open', 0.7, 0.5), hand('open', 0.3 - i * 0.02, 0.5)]),  // the other opens too
      [hand('open', 0.7, 0.5), hand('fist', 0.2, 0.5)],
    ], repeat(11, () => ['Right', 'Left']));
    expect(r.frames[9].sweep!.at.x).toBeGreaterThan(0.75);             // still the sweeping hand, not the steering one
    expect(r.frames[10].sweep).toEqual({ at: r.frames[10].sweep!.at, open: false });
  });
});


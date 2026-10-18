import { describe, expect, it } from 'vitest';
import { FIST, OPEN, PINCH, POINT, HAND_EDGES } from '../data/handPoses';
import { MOVES, frameFor } from '../data/guideMoves';

const samples = Array.from({ length: 41 }, (_, i) => i / 40);

describe('the hand guide', () => {
  it('every pose is a full MediaPipe hand (21 points) and the bones reach only real joints', () => {
    for (const pose of [OPEN, POINT, PINCH, FIST]) expect(pose).toHaveLength(21);
    expect(HAND_EDGES.flat().every((i) => i >= 0 && i < 21)).toBe(true);
  });

  it('every move animates hands with full poses at every moment of its loop', () => {
    for (const m of MOVES) {
      for (const u of samples) {
        const f = frameFor(m.key, u);
        expect(f.hands.length, m.key).toBeGreaterThan(0);
        f.hands.forEach((h) => expect(h.pose).toHaveLength(21));
      }
    }
  });

  it('zoom and the sweeps show two hands; the one-hand moves show one', () => {
    for (const k of ['zoom', 'clear', 'back', 'snapshot'] as const) expect(frameFor(k, 0.5).hands).toHaveLength(2);
    for (const k of ['rest', 'turn', 'move', 'select'] as const) expect(frameFor(k, 0.5).hands).toHaveLength(1);
  });

  it('turning turns the model, zooming grows it', () => {
    expect(frameFor('turn', 0.75).model.rotY).toBeGreaterThan(1);
    expect(frameFor('zoom', 0.75).model.scale).toBeGreaterThan(1.3);
  });

  it('select: the ring fills while the point holds, then the part is selected', () => {
    expect(frameFor('select', 0.4).ring).toBeGreaterThan(0.3);
    expect(frameFor('select', 0.4).selected).toBe(false);
    expect(frameFor('select', 0.9).selected).toBe(true);
  });

  it('clear: the rest moves the way the other hand sweeps, then is gone; back brings it in', () => {
    const early = frameFor('clear', 0.1), mid = frameFor('clear', 0.45), end = frameFor('clear', 0.99);
    const handDx = mid.hands[1].at[0] - early.hands[1].at[0];
    expect(Math.sign(mid.rest.dx)).toBe(Math.sign(handDx));      // same direction as the hand
    expect(end.rest.opacity).toBeCloseTo(0, 3);
    expect(frameFor('back', 0.0).rest.opacity).toBeCloseTo(0, 3);
    expect(frameFor('back', 0.99).rest.dx).toBeCloseTo(0, 3);
    expect(frameFor('back', 0.99).rest.opacity).toBeGreaterThan(0.4);
  });

  it('clear and back: the palm is held still until its ring is full, and only then sweeps (v3.1)', () => {
    for (const k of ['clear', 'back'] as const) {
      const start = frameFor(k, 0).hands[1].at[0];
      for (const u of samples) {
        const f = frameFor(k, u);
        if (Math.abs(f.hands[1].at[0] - start) > 1e-6) expect(f.palmRing, `${k} at ${u}`).toBe(1);
      }
      expect(frameFor(k, 0.5).palmRing, k).toBe(1);
      expect(frameFor(k, 0.5).hands[1].at[0], k).not.toBeCloseTo(start, 2);
    }
  });

  it('snapshot: the two pointing hands meet, and only then the flash', () => {
    const gap = (u: number) => { const f = frameFor('snapshot', u); return f.hands[0].at[0] - f.hands[1].at[0]; };
    expect(gap(0.0)).toBeGreaterThan(gap(0.5));
    expect(gap(0.5)).toBeCloseTo(2.5, 2);                  // tips 1.2 from each origin: 0.1 apart
    expect(frameFor('snapshot', 0.52).flash).toBeCloseTo(1, 5);
    for (const u of samples) if (frameFor('snapshot', u).flash > 0) expect(gap(u)).toBeCloseTo(2.5, 2);
  });

  it('the snapshot move is for the atlas only', () => {
    expect(MOVES.filter((m) => m.atlasOnly).map((m) => m.key)).toEqual(['snapshot']);
  });
});

import { describe, expect, it } from 'vitest';
import { stageFollow, TILT_FADE, TILT_START } from './stageFollow';

const at = { x: 0, y: 0, scale: 0.8, fit: 0.8, rotX: 0 };   // the auto-fitted body, untouched

describe('the stand goes with the body', () => {
  it('at rest it is where it always was, full size, fully there', () => {
    expect(stageFollow(at)).toEqual({ x: 0, y: 0, scale: 1, opacity: 1 });
  });

  it('a hand pan carries it along; a hand zoom scales it with the body', () => {
    const f = stageFollow({ ...at, x: 1.4, y: -0.6, scale: 0.8 * 2.5 });
    expect(f.x).toBe(1.4);
    expect(f.y).toBe(-0.6);
    expect(f.scale).toBeCloseTo(2.5, 9);       // the gesture zoom alone, not the auto-fit
  });

  it('turning the body to the side keeps it (the stand is round): only a tilt fades it', () => {
    expect(stageFollow({ ...at, rotX: TILT_START }).opacity).toBe(1);
    expect(stageFollow({ ...at, rotX: -0.4 }).opacity).toBe(1);
    expect(stageFollow({ ...at, rotX: TILT_START + TILT_FADE / 2 }).opacity).toBeCloseTo(0.5, 9);
    expect(stageFollow({ ...at, rotX: -(TILT_START + TILT_FADE) }).opacity).toBe(0);
    expect(stageFollow({ ...at, rotX: Math.PI / 2 }).opacity).toBe(0);
  });

  it('the tilt values are the ones documented: holds to 0.45 rad, gone by 0.75', () => {
    expect(stageFollow({ ...at, rotX: 0.44 }).opacity).toBe(1);
    expect(stageFollow({ ...at, rotX: 0.76 }).opacity).toBe(0);
  });
});

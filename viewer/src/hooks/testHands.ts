// Synthetic MediaPipe hands for the gesture tests (gestureEngine, handsPipeline): no camera, so
// every rule is driven through real landmark sequences.
import { initialEngine, step, type EngineState, type Frame, type NormalizedLandmark, type Pose } from './gestureEngine';

/** A synthetic MediaPipe hand (21 landmarks, camera coordinates, y down) in a given pose, with
 *  its knuckles centred on (cx, cy). Extended fingers point up; curled tips sit in the palm. */
export function hand(pose: Exclude<Pose, 'other'>, cx = 0.5, cy = 0.5, thumbGap?: number): NormalizedLandmark[] {
  const p = (x: number, y: number) => ({ x: cx + x, y: cy + y, z: 0 });
  const lm: NormalizedLandmark[] = new Array(21);
  lm[0] = p(0, 0.1); // wrist
  const mcp: [number, number][] = [[-0.03, 0], [0, -0.005], [0.03, 0], [0.055, 0.01]];
  const up = { index: pose === 'open' || pose === 'point', rest: pose === 'open' || pose === 'pinch' };
  mcp.forEach(([x, y], f) => {
    const i = 5 + f * 4;
    const ext = f === 0 ? up.index : up.rest;
    lm[i] = p(x, y);
    if (ext) { lm[i + 1] = p(x, y - 0.035); lm[i + 2] = p(x, y - 0.055); lm[i + 3] = p(x, y - 0.075); }
    else { lm[i + 1] = p(x, y - 0.02); lm[i + 2] = p(x, y); lm[i + 3] = p(x + 0.005, y + 0.03); }
  });
  if (pose === 'pinch') {
    // index bent toward the thumb; thumb tip meets index tip
    lm[6] = p(-0.035, -0.03); lm[7] = p(-0.04, -0.035); lm[8] = p(-0.045, -0.03);
    const gap = thumbGap ?? 0.006;
    lm[1] = p(-0.04, 0.07); lm[2] = p(-0.055, 0.04); lm[3] = p(-0.055, 0.0); lm[4] = p(-0.045 - gap, -0.03);
  } else if (pose === 'fist') {
    lm[1] = p(-0.04, 0.07); lm[2] = p(-0.045, 0.04); lm[3] = p(-0.035, 0.02); lm[4] = p(-0.022, 0.018);
  } else {
    lm[1] = p(-0.04, 0.07); lm[2] = p(-0.06, 0.05); lm[3] = p(-0.075, 0.03); lm[4] = p(-0.085, 0.01);
  }
  return lm;
}

/** Drive the engine through frames 16 ms apart; returns every frame and the final state. */
export function run(frames: (NormalizedLandmark[][] | null)[], labels: string[][] = [], start: EngineState = initialEngine()) {
  let state = start;
  const out: Frame[] = [];
  frames.forEach((hands, i) => {
    const r = step(state, hands, labels[i] ?? (hands ? hands.map(() => 'Right') : undefined), i * 16);
    state = r.state;
    out.push(r.frame);
  });
  return { state, frames: out };
}

export const repeat = <T,>(n: number, f: (i: number) => T): T[] => Array.from({ length: n }, (_, i) => f(i));

/** The same hand turned in the image plane by `deg` about its middle knuckle: a palm held at an
 *  angle, or sideways like a wiping hand (90). */
export function rotate(lms: NormalizedLandmark[], deg: number): NormalizedLandmark[] {
  const c = lms[9], a = (deg * Math.PI) / 180;
  return lms.map((p) => ({
    x: c.x + (p.x - c.x) * Math.cos(a) - (p.y - c.y) * Math.sin(a),
    y: c.y + (p.x - c.x) * Math.sin(a) + (p.y - c.y) * Math.cos(a),
    z: p.z,
  }));
}

/** The same hand nearer or further from the camera: scaled by `k` about its middle knuckle. */
export function scale(lms: NormalizedLandmark[], k: number): NormalizedLandmark[] {
  const c = lms[9];
  return lms.map((p) => ({ x: c.x + (p.x - c.x) * k, y: c.y + (p.y - c.y) * k, z: p.z }));
}

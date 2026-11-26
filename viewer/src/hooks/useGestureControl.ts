import { useCallback, useRef, useState } from 'react';
import type { NormalizedLandmark } from '../components/HandTracker';
import { initialEngine, step, type ActiveGesture, type Pose, type Pt } from './gestureEngine';

export type { ActiveGesture } from './gestureEngine';

/** What the hologram renders from the hands, one snapshot per camera frame (gestureEngine.ts). */
export interface GestureState {
  activeGesture: ActiveGesture;
  pose: Pose | null;
  /** the primary hand's index tip, mirrored to the screen, 0..1 (null: no cursor) */
  cursor: Pt | null;
  /** a drag or zoom is steering the model right now */
  grabbing: boolean;
  /** both hands pinching */
  zooming: boolean;
  /** the other hand's palm (mirrored, 0..1) and pose while it is up: sweeps (selectionEngine) */
  second: Pt | null;
  secondPose: Pose | null;
  /** the hand that can sweep (either hand, an open palm at any angle; gestureEngine v3.3) */
  sweep: { at: Pt; open: boolean } | null;
  /** both index fingers pointing at each other right now */
  snapPose: boolean;
  /** counts AI snapshot gestures (once per touch); a consumer captures when it changes */
  snapshotId: number;
  modelPosition: Pt;
  modelRotationX: number;
  modelRotationY: number;
  modelScale: number;
  handsVisible: boolean;
}

const START: GestureState = {
  activeGesture: 'NONE', pose: null, cursor: null, grabbing: false, zooming: false, second: null, secondPose: null, sweep: null,
  snapPose: false, snapshotId: 0,
  modelPosition: { x: 0, y: 0 }, modelRotationX: 0, modelRotationY: 0, modelScale: 1, handsVisible: false,
};

/** Feeds MediaPipe's landmarks through the gesture engine and keeps one rendered snapshot. */
export function useGestureControl(): {
  gestureState: GestureState;
  /** feed one camera frame; returns the snapshot it rendered (for per-frame logic in callbacks) */
  updateHands: (hands: NormalizedLandmark[][] | null, handedness?: string[]) => GestureState;
} {
  const [gestureState, setGestureState] = useState<GestureState>(START);
  const engine = useRef(initialEngine());
  const snapshots = useRef(0);

  const updateHands = useCallback((hands: NormalizedLandmark[][] | null, handedness?: string[]) => {
    const { state, frame } = step(engine.current, hands, handedness, performance.now());
    engine.current = state;
    if (frame.snapshot) snapshots.current += 1;
    const next: GestureState = {
      activeGesture: frame.gesture,
      pose: frame.pose,
      cursor: frame.cursor,
      grabbing: frame.grabbing,
      zooming: frame.zooming,
      second: frame.second,
      secondPose: frame.secondPose,
      sweep: frame.sweep,
      snapPose: frame.snapPose,
      snapshotId: snapshots.current,
      modelPosition: state.pos,
      modelRotationX: state.rotX,
      modelRotationY: state.rotY,
      modelScale: state.scale,
      handsVisible: frame.handsVisible,
    };
    setGestureState(next);
    return next;
  }, []);

  return { gestureState, updateHands };
}

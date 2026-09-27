import { useState, useRef, useCallback } from 'react';
import type { NormalizedLandmark } from '../components/HandTracker';

export type ActiveGesture = 'ROTATE' | 'PAN' | 'ZOOM' | 'FREEZE' | 'NONE';

export interface GestureState {
  activeGesture: ActiveGesture;
  modelPosition: { x: number; y: number };
  modelRotationX: number;
  modelRotationY: number;
  modelScale: number;
  handsVisible: boolean;
  isFrozen: boolean;
  fistHoldProgress: number;
  leftIndexFingerTip?: { x: number; y: number };
  rightIndexFingerTip?: { x: number; y: number };
  leftHandPinchActive: boolean;
  leftFistActive: boolean;
  leftVictoryActive: boolean;
  swipeGesture: 'LEFT' | 'RIGHT' | 'DOWN' | null;
  rightSwipeGesture: 'LEFT' | 'RIGHT' | 'DOWN' | null;
}

// ── Smoothing ──────────────────────────────────────────────────────────────────
// Low-pass exponential smoothing. Higher = more smoothing, more lag.
const SMOOTH = 0.55;

function smoothHand(
  raw: NormalizedLandmark[],
  prev: NormalizedLandmark[] | undefined
): NormalizedLandmark[] {
  if (!prev || prev.length !== raw.length) return raw;
  return raw.map((lm, i) => ({
    x: SMOOTH * prev[i].x + (1 - SMOOTH) * lm.x,
    y: SMOOTH * prev[i].y + (1 - SMOOTH) * lm.y,
    z: SMOOTH * prev[i].z + (1 - SMOOTH) * lm.z,
  }));
}

// ── Geometry helpers ───────────────────────────────────────────────────────────
type Pt = { x: number; y: number };

function lmDist(a: NormalizedLandmark, b: NormalizedLandmark): number {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
}

function ptDist(a: Pt, b: Pt): number {
  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
}

// Palm centre = average of wrist + 4 MCP joints (stable, low-jitter)
function palmCenter(lms: NormalizedLandmark[]): Pt {
  const pts = [lms[0], lms[5], lms[9], lms[13], lms[17]];
  const sumX = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const sumY = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  return { x: 1 - sumX, y: sumY }; // mirror x for natural left/right feel
}

// Pinch midpoint (thumb tip + index tip, mirrored)
function pinchMidpoint(lms: NormalizedLandmark[]): Pt {
  return {
    x: 1 - (lms[4].x + lms[8].x) / 2,
    y: (lms[4].y + lms[8].y) / 2,
  };
}

// ── Gesture detectors ──────────────────────────────────────────────────────────
//
// Finger extended: tip is ABOVE its MCP knuckle in screen space.
// MediaPipe y increases downward, so tip.y < mcp.y = extended.
// We check two conditions for robustness:
//   1. tip above mcp (primary)
//   2. tip above pip (secondary, guards against half-curled)
const FINGER_JOINTS: [tip: number, pip: number, mcp: number][] = [
  [8,  7,  5],   // index
  [12, 11,  9],  // middle
  [16, 15, 13],  // ring
  [20, 19, 17],  // pinky
];

function countExtended(lms: NormalizedLandmark[]): number {
  return FINGER_JOINTS.filter(
    ([tip, pip, mcp]) => lms[tip].y < lms[pip].y && lms[tip].y < lms[mcp].y
  ).length;
}

// PINCH: thumb tip close to index tip
const PINCH_ON  = 0.05;   // activate below this
const PINCH_OFF = 0.075;  // deactivate above this (hysteresis)

function getPinchDist(lms: NormalizedLandmark[]): number {
  return lmDist(lms[4], lms[8]);
}

// OPEN PALM: 4+ fingers clearly extended, not pinching
// Uses a slightly relaxed threshold (3+) so partial spread still registers
function isOpenPalm(lms: NormalizedLandmark[], isPinching: boolean): boolean {
  return !isPinching && countExtended(lms) >= 3;
}

// FIST: all 4 fingers curled and hand clenched (clenchRatio < 0.90)
// This is independent of pinch status to avoid false-negative fists due to 2D projection overlap.
function getClenchRatio(lms: NormalizedLandmark[]): number {
  const wrist = lms[0];
  const middleMCP = lms[9];
  
  // Palm length serves as a scale-invariant reference
  const palmLength = lmDist(wrist, middleMCP);
  if (palmLength === 0) return 1.0;

  // Palm center: average of wrist and the 4 MCP joints
  const pts = [lms[0], lms[5], lms[9], lms[13], lms[17]];
  const palmX = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const palmY = pts.reduce((s, p) => s + p.y, 0) / pts.length;

  // Average distance of 4 finger tips (index, middle, ring, pinky) to palm center
  const tips = [lms[8], lms[12], lms[16], lms[20]];
  const totalTipDist = tips.reduce((sum, tip) => {
    const dx = tip.x - palmX;
    const dy = tip.y - palmY;
    return sum + Math.sqrt(dx * dx + dy * dy);
  }, 0);

  return (totalTipDist / 4) / palmLength;
}

function isFist(lms: NormalizedLandmark[]): boolean {
  return countExtended(lms) === 0 && getClenchRatio(lms) < 0.90;
}

// Victory sign: index and middle fingers extended, ring and pinky curled
function isVictory(landmarks: NormalizedLandmark[]): boolean {
  const indexExtended = landmarks[8].y < landmarks[6].y; // index tip above PIP
  const middleExtended = landmarks[12].y < landmarks[10].y; // middle tip above PIP
  const ringCurled = landmarks[16].y > landmarks[14].y; // ring tip below PIP
  const pinkyCurled = landmarks[20].y > landmarks[18].y; // pinky tip below PIP
  return indexExtended && middleExtended && ringCurled && pinkyCurled;
}

// ── Tuning constants ───────────────────────────────────────────────────────────
const FIST_FRAMES     = 22;    // frames to hold fist to toggle freeze (~360ms @ 60fps)
const FREEZE_COOLDOWN = 2000;  // ms minimum between freeze toggles
const ROT_SENS_Y      = 3.2;   // yaw sensitivity (reduced from 7.0)
const ROT_SENS_X      = 2.2;   // pitch sensitivity (reduced from 4.5)
const PAN_SENS        = 5.0;   // pan sensitivity (scene units / normalised unit)
const ZOOM_SENS       = 3.5;   // exponential zoom sensitivity
const SCALE_MIN       = 0.08;
const SCALE_MAX       = 60.0;

// ── Hook ───────────────────────────────────────────────────────────────────────
export function useGestureControl(): {
  gestureState: GestureState;
  updateHands: (hands: NormalizedLandmark[][] | null, handedness?: string[]) => void;
  clearSwipeGestures: () => void;
} {
  // Rendered state (causes re-renders, kept minimal)
  const [activeGesture,  setActiveGesture]  = useState<ActiveGesture>('NONE');
  const [modelPosition,  setModelPosition]  = useState<Pt>({ x: 0, y: 0 });
  const [modelRotationX, setModelRotationX] = useState(0);
  const [modelRotationY, setModelRotationY] = useState(0);
  const [modelScale,     setModelScale]     = useState(1.0);
  const [handsVisible,   setHandsVisible]   = useState(false);
  const [isFrozen,       setIsFrozen]       = useState(false);
  const [fistProgress,   setFistProgress]   = useState(0);

  // Raycast & Swipe Diagnostic States
  const [leftIndexFingerTip, setLeftIndexFingerTip] = useState<{ x: number; y: number } | undefined>(undefined);
  const [rightIndexFingerTip, setRightIndexFingerTip] = useState<{ x: number; y: number } | undefined>(undefined);
  const [leftHandPinchActive, setLeftHandPinchActive] = useState(false);
  const [leftFistActive, setLeftFistActive] = useState(false);
  const [leftVictoryActive, setLeftVictoryActive] = useState(false);
  const [swipeGesture, setSwipeGesture] = useState<'LEFT' | 'RIGHT' | 'DOWN' | null>(null);
  const [rightSwipeGesture, setRightSwipeGesture] = useState<'LEFT' | 'RIGHT' | 'DOWN' | null>(null);

  // Stable transform refs (avoid stale closures)
  const frozenRef = useRef(false);
  const posRef    = useRef<Pt>({ x: 0, y: 0 });
  const rotXRef   = useRef(0);
  const rotYRef   = useRef(0);
  const scaleRef  = useRef(1.0);

  // Pinch hysteresis state per hand (true = currently pinching)
  const pinchActive = useRef<boolean[]>([false, false]);

  // Delta-based tracking: previous frame positions
  const prevRotPt  = useRef<Pt | null>(null);
  const prevPanPt  = useRef<Pt | null>(null);

  // Two-hand zoom anchor
  const zoomDistRef  = useRef<number | null>(null);
  const zoomBaseScale = useRef(1.0);
  const zoomBasePos  = useRef<Pt>({ x: 0, y: 0 });

  // Fist freeze state machine
  const fistPhase  = useRef<'idle' | 'counting' | 'fired'>('idle');
  const fistCount  = useRef(0);
  const lastToggle = useRef(0);

  // Landmark smoothing cache
  const smoothCache = useRef<Map<number, NormalizedLandmark[]>>(new Map());

  // Left wrist history for swipe detection
  const leftWristHistory = useRef<{ x: number; y: number }[]>([]);

  // Right wrist history for swipe detection
  const rightWristHistory = useRef<{ x: number; y: number }[]>([]);

  // Clear all active gesture anchors when switching modes
  const clearAnchors = useCallback(() => {
    prevRotPt.current   = null;
    prevPanPt.current   = null;
    zoomDistRef.current = null;
  }, []);

  const updateHands = useCallback(
    (rawHands: NormalizedLandmark[][] | null, rawHandedness?: string[]) => {

      // ── No hands ────────────────────────────────────────────────────────
      if (!rawHands || rawHands.length === 0) {
        smoothCache.current.clear();
        clearAnchors();
        pinchActive.current = [false, false];
        fistPhase.current   = 'idle';
        fistCount.current   = 0;
        setFistProgress(0);
        setHandsVisible(false);
        setLeftIndexFingerTip(undefined);
        setRightIndexFingerTip(undefined);
        setLeftHandPinchActive(false);
        setLeftFistActive(false);
        setLeftVictoryActive(false);
        leftWristHistory.current = [];
        rightWristHistory.current = [];
        setSwipeGesture(null);
        setRightSwipeGesture(null);
        setActiveGesture(frozenRef.current ? 'FREEZE' : 'NONE');
        return;
      }

      setHandsVisible(true);

      // Smooth each hand independently
      const hands = rawHands.map((raw, idx) => {
        const prev = smoothCache.current.get(idx);
        const sm   = smoothHand(raw, prev);
        smoothCache.current.set(idx, sm);
        return sm;
      });

      // ── Resolve handedness ───────────────────────────────────────────────
      let rightHand: NormalizedLandmark[] | null = null;
      let leftHand:  NormalizedLandmark[] | null = null;

      hands.forEach((h, i) => {
        const label = rawHandedness?.[i] ?? '';
        if (label === 'Right')      rightHand = h;
        else if (label === 'Left')  leftHand  = h;
      });

      // Fallback when handedness is unavailable
      if (!rightHand && !leftHand) {
        rightHand = hands[0] ?? null;
        leftHand  = hands.length >= 2 ? hands[1] : null;
      }

      // ── Per-hand pinch state (with hysteresis) ───────────────────────────
      const updatePinch = (lms: NormalizedLandmark[], slot: 0 | 1): boolean => {
        const d = getPinchDist(lms);
        if (pinchActive.current[slot]) {
          pinchActive.current[slot] = d < PINCH_OFF; // release with wider threshold
        } else {
          pinchActive.current[slot] = d < PINCH_ON;  // activate with tight threshold
        }
        return pinchActive.current[slot];
      };

      const rPinch = rightHand ? updatePinch(rightHand, 0) : (pinchActive.current[0] = false);
      const lPinch = leftHand  ? updatePinch(leftHand,  1) : (pinchActive.current[1] = false);

      // ── Set finger tips, pinch, victory and fist states ─────────────────
      if (leftHand) {
        setLeftIndexFingerTip({ x: 1 - leftHand[8].x, y: leftHand[8].y });
        // Hysteresis-stabilised pinch (kept for two-hand zoom compatibility)
        setLeftHandPinchActive(lPinch as boolean);
        setLeftFistActive(isFist(leftHand));
        // Victory/peace ✌️ — primary select gesture
        setLeftVictoryActive(isVictory(leftHand));
      } else {
        setLeftIndexFingerTip(undefined);
        setLeftHandPinchActive(false);
        setLeftFistActive(false);
        setLeftVictoryActive(false);
      }

      if (rightHand) {
        setRightIndexFingerTip({ x: 1 - rightHand[8].x, y: rightHand[8].y });
      } else {
        setRightIndexFingerTip(undefined);
      }

      // ── Left hand swipe detection (Freeze mode only) ──────────────────────
      if (frozenRef.current && leftHand) {
        const wrist = { x: 1 - leftHand[0].x, y: leftHand[0].y };
        leftWristHistory.current.push(wrist);
        if (leftWristHistory.current.length > 10) {
          leftWristHistory.current.shift();
        }

        if (leftWristHistory.current.length >= 6) {
          const history = leftWristHistory.current;
          let consecutiveLeft = 0;
          let consecutiveRight = 0;
          let consecutiveDown = 0;

          for (let i = 1; i < history.length; i++) {
            const dx = history[i].x - history[i - 1].x;
            const dy = history[i].y - history[i - 1].y;

            if (dx < -0.04) {
              consecutiveLeft++;
            } else {
              consecutiveLeft = 0;
            }

            if (dx > 0.04) {
              consecutiveRight++;
            } else {
              consecutiveRight = 0;
            }

            if (dy > 0.04) {
              consecutiveDown++;
            } else {
              consecutiveDown = 0;
            }

            if (consecutiveLeft >= 5) {
              setSwipeGesture('LEFT');
              leftWristHistory.current = [];
              break;
            }
            if (consecutiveRight >= 5) {
              setSwipeGesture('RIGHT');
              leftWristHistory.current = [];
              break;
            }
            if (consecutiveDown >= 5) {
              setSwipeGesture('DOWN');
              leftWristHistory.current = [];
              break;
            }
          }
        }
      } else {
        leftWristHistory.current = [];
      }

      // ── Right hand swipe detection (Freeze mode only) ─────────────────────
      if (frozenRef.current && rightHand) {
        const wrist = { x: 1 - rightHand[0].x, y: rightHand[0].y };
        rightWristHistory.current.push(wrist);
        if (rightWristHistory.current.length > 10) {
          rightWristHistory.current.shift();
        }

        if (rightWristHistory.current.length >= 6) {
          const history = rightWristHistory.current;
          let consecutiveLeft = 0;
          let consecutiveRight = 0;
          let consecutiveDown = 0;

          for (let i = 1; i < history.length; i++) {
            const dx = history[i].x - history[i - 1].x;
            const dy = history[i].y - history[i - 1].y;

            if (dx < -0.04) {
              consecutiveLeft++;
            } else {
              consecutiveLeft = 0;
            }

            if (dx > 0.04) {
              consecutiveRight++;
            } else {
              consecutiveRight = 0;
            }

            if (dy > 0.04) {
              consecutiveDown++;
            } else {
              consecutiveDown = 0;
            }

            if (consecutiveLeft >= 5) {
              setRightSwipeGesture('LEFT');
              rightWristHistory.current = [];
              break;
            }
            if (consecutiveRight >= 5) {
              setRightSwipeGesture('RIGHT');
              rightWristHistory.current = [];
              break;
            }
            if (consecutiveDown >= 5) {
              setRightSwipeGesture('DOWN');
              rightWristHistory.current = [];
              break;
            }
          }
        }
      } else {
        rightWristHistory.current = [];
      }

      // ── TWO-HAND ZOOM (highest priority) ────────────────────────────────
      if (rightHand && leftHand && rPinch && lPinch) {
        // Cancel single-hand anchors
        prevRotPt.current = null;
        prevPanPt.current = null;
        fistPhase.current = 'idle';
        fistCount.current = 0;
        setFistProgress(0);

        const c0 = pinchMidpoint(rightHand);
        const c1 = pinchMidpoint(leftHand);
        const d  = ptDist(c0, c1);

        if (zoomDistRef.current === null) {
          // First frame of zoom: set anchors
          zoomDistRef.current  = d;
          zoomBaseScale.current = scaleRef.current;
          zoomBasePos.current  = { ...posRef.current };
        } else {
          const delta     = d - zoomDistRef.current;
          const nextScale = Math.max(
            SCALE_MIN,
            Math.min(SCALE_MAX, zoomBaseScale.current * Math.exp(delta * ZOOM_SENS))
          );
          const ratio = nextScale / scaleRef.current;

          // Pivot around midpoint of the two pinch points (in scene space)
          const mx = (c0.x + c1.x) / 2 - 0.5;
          const my = 0.5 - (c0.y + c1.y) / 2;
          const hx = mx * 6.5;
          const hy = my * 4.5;
          const dx = hx - posRef.current.x;
          const dy = hy - posRef.current.y;

          posRef.current   = { x: posRef.current.x + (1 - ratio) * dx,
                               y: posRef.current.y + (1 - ratio) * dy };
          scaleRef.current = nextScale;
          setModelScale(nextScale);
          setModelPosition({ ...posRef.current });
        }

        setActiveGesture('ZOOM');
        return;
      } else {
        // Reset zoom anchor as soon as one hand releases pinch
        zoomDistRef.current = null;
      }

      // ── FROZEN: only right hand fist-hold can unfreeze ──────────────────
      if (frozenRef.current) {
        const h = rightHand;
        if (h) {
          const fist     = isFist(h);
          const cooldown = Date.now() - lastToggle.current > FREEZE_COOLDOWN;

          if (fist && cooldown) {
            fistPhase.current === 'idle' && (fistPhase.current = 'counting');
            if (fistPhase.current === 'counting') {
              fistCount.current++;
              setFistProgress(Math.min(1, fistCount.current / FIST_FRAMES));
              if (fistCount.current >= FIST_FRAMES) {
                frozenRef.current  = false;
                setIsFrozen(false);
                setSwipeGesture(null); // Clear swipe when exiting freeze mode
                setRightSwipeGesture(null);
                lastToggle.current = Date.now();
                fistPhase.current  = 'fired';
                fistCount.current  = 0;
                clearAnchors();
                setFistProgress(0);
                setActiveGesture('NONE');
                return;
              }
            }
          } else if (!fist) {
            if (fistPhase.current !== 'fired') {
              fistPhase.current = 'idle';
              fistCount.current = 0;
              setFistProgress(0);
            } else {
              fistPhase.current = 'idle'; // reset after fired
            }
          }
        }
        setActiveGesture('FREEZE');
        return;
      }

      // ── Single-hand: use right hand, fall back to left ───────────────────
      const h = rightHand ?? leftHand;
      if (!h) {
        setActiveGesture('NONE');
        return;
      }

      const pinching = rPinch || lPinch;

      // ── FIST: hold to freeze (right hand only) ───────────────────────────
      if (rightHand && isFist(rightHand)) {
        const cooldown = Date.now() - lastToggle.current > FREEZE_COOLDOWN;

        // Cancel any active gesture anchors while building toward freeze
        prevRotPt.current = null;
        prevPanPt.current = null;

        if (cooldown) {
          if (fistPhase.current === 'idle') {
            fistPhase.current = 'counting';
            fistCount.current = 1;
            setFistProgress(1 / FIST_FRAMES);
          } else if (fistPhase.current === 'counting') {
            fistCount.current++;
            setFistProgress(Math.min(1, fistCount.current / FIST_FRAMES));
            if (fistCount.current >= FIST_FRAMES) {
              frozenRef.current  = true;
              setIsFrozen(true);
              lastToggle.current = Date.now();
              fistPhase.current  = 'fired';
              fistCount.current  = 0;
              setFistProgress(0);
              setActiveGesture('FREEZE');
              return;
            }
          }
          // While counting, show a transitional NONE state (progress bar visible)
          setActiveGesture('NONE');
        } else {
          // Cooldown not elapsed — treat as idle
          fistPhase.current = 'idle';
          fistCount.current = 0;
          setFistProgress(0);
          setActiveGesture('NONE');
        }
        return;
      }

      // Fist released — reset state machine (unless fired)
      if (fistPhase.current !== 'fired') {
        fistPhase.current = 'idle';
        fistCount.current = 0;
        setFistProgress(0);
      } else {
        fistPhase.current = 'idle';
      }

      // ── ROTATE: single-hand pinch + move ────────────────────────────────
      // Clear pan anchor so switching gestures doesn't cause a jump
      if (pinching) {
        if (prevPanPt.current !== null) {
          prevPanPt.current = null;
        }

        const pt = pinchMidpoint(h);

        if (prevRotPt.current === null) {
          // First pinch frame — just record position, don't move yet
          prevRotPt.current = pt;
        } else {
          const dx = pt.x - prevRotPt.current.x;
          const dy = pt.y - prevRotPt.current.y;

          rotYRef.current += dx * ROT_SENS_Y;
          rotXRef.current  = Math.max(
            -Math.PI * 0.5,
            Math.min(Math.PI * 0.5, rotXRef.current + dy * ROT_SENS_X)
          );

          setModelRotationY(rotYRef.current);
          setModelRotationX(rotXRef.current);
          prevRotPt.current = pt;
        }

        setActiveGesture('ROTATE');
        return;
      }

      // Not pinching — clear rotate anchor
      prevRotPt.current = null;

      // ── PAN: open palm + move ────────────────────────────────────────────
      if (isOpenPalm(h, pinching)) {
        const pt = palmCenter(h);

        if (prevPanPt.current === null) {
          // First open-palm frame — just record, don't jump
          prevPanPt.current = pt;
        } else {
          const dx = pt.x - prevPanPt.current.x;
          const dy = pt.y - prevPanPt.current.y;

          posRef.current = {
            x: posRef.current.x + dx * PAN_SENS,
            y: posRef.current.y - dy * PAN_SENS, // invert y: up in screen = up in scene
          };
          setModelPosition({ ...posRef.current });
          prevPanPt.current = pt;
        }

        setActiveGesture('PAN');
        return;
      }

      // Open palm released — clear anchor
      prevPanPt.current = null;

      // ── Idle (no recognisable gesture) ──────────────────────────────────
      setActiveGesture('NONE');
    },
    [clearAnchors]
  );

  const gestureState: GestureState = {
    activeGesture,
    modelPosition,
    modelRotationX,
    modelRotationY,
    modelScale,
    handsVisible,
    isFrozen,
    fistHoldProgress: fistProgress,
    leftIndexFingerTip,
    rightIndexFingerTip,
    leftHandPinchActive,
    leftFistActive,
    leftVictoryActive,
    swipeGesture,
    rightSwipeGesture,
  };

  const clearSwipeGestures = useCallback(() => {
    setSwipeGesture(null);
    setRightSwipeGesture(null);
  }, []);

  return { gestureState, updateHands, clearSwipeGestures };
}

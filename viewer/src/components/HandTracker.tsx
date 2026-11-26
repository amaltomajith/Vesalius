import { useRef, useEffect, useCallback } from 'react';
import { initialLock, lockStep } from '../hooks/operatorLock';

// Landmark index constants
const HAND_CONNECTIONS: [number, number][] = [
  // Thumb
  [0, 1], [1, 2], [2, 3], [3, 4],
  // Index
  [0, 5], [5, 6], [6, 7], [7, 8],
  // Middle
  [0, 9], [9, 10], [10, 11], [11, 12],
  // Ring
  [0, 13], [13, 14], [14, 15], [15, 16],
  // Pinky
  [0, 17], [17, 18], [18, 19], [19, 20],
  // Palm
  [5, 9], [9, 13], [13, 17],
];

const FINGER_TIPS = [4, 8, 12, 16, 20];
const WRIST_IDX = 0;

export interface NormalizedLandmark {
  x: number;
  y: number;
  z: number;
}

export interface HandTrackerProps {
  /** Called every frame with up to 2 hands of landmarks (or null if none): the operator's, when
   *  more people are in view (hooks/operatorLock.ts) */
  onHandsUpdate: (hands: NormalizedLandmark[][] | null, handedness?: string[]) => void;
  /** how many other people's hands are being ignored (0 when the operator is alone) */
  onIgnored?: (count: number) => void;
  /** camera state, for the HUD: starting -> on | blocked | unavailable */
  onStatus?: (status: CameraStatus) => void;
}

export type CameraStatus = 'starting' | 'on' | 'blocked' | 'unavailable';

export default function HandTracker({ onHandsUpdate, onStatus, onIgnored }: HandTrackerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const landmarkerRef = useRef<any>(null);
  const lastVideoTimeRef = useRef(-1);
  const animFrameRef = useRef<number>(0);
  const activeRef = useRef(true);
  // whose hands to follow when others are in view (nearest, then follow; user decision 2026-10-18)
  const lockRef = useRef(initialLock());
  const ignoredRef = useRef(0);

  // Resize canvas on window resize
  useEffect(() => {
    const resize = () => {
      const c = canvasRef.current;
      if (c) { c.width = window.innerWidth; c.height = window.innerHeight; }
    };
    window.addEventListener('resize', resize);
    resize();
    return () => window.removeEventListener('resize', resize);
  }, []);

  // Draw hand skeleton on the overlay canvas
  const drawHands = useCallback((
    ctx: CanvasRenderingContext2D,
    handsLandmarks: NormalizedLandmark[][],
    w: number,
    h: number,
    kept: number[],
  ) => {
    ctx.clearRect(0, 0, w, h);

    handsLandmarks.forEach((landmarks, idx0) => {
      // everyone else's hands: a faint grey outline, so it is visible whose hands are followed
      if (!kept.includes(idx0)) {
        ctx.strokeStyle = 'rgba(138, 154, 148, 0.35)';
        ctx.lineWidth = 1.5;
        ctx.lineCap = 'round';
        for (const [a, b] of HAND_CONNECTIONS) {
          ctx.beginPath();
          ctx.moveTo((1 - landmarks[a].x) * w, landmarks[a].y * h);
          ctx.lineTo((1 - landmarks[b].x) * w, landmarks[b].y * h);
          ctx.stroke();
        }
        return;
      }
      const handIdx = kept.indexOf(idx0);
      const handColor = handIdx === 0 ? '#c9f2df' : '#9fd8ff';
      const tipColor = handIdx === 0 ? '#7ee2b3' : '#ffb59f';

      // Draw connections
      for (const [a, b] of HAND_CONNECTIONS) {
        const lmA = landmarks[a];
        const lmB = landmarks[b];
        if (!lmA || !lmB) continue;
        const ax = (1 - lmA.x) * w;
        const ay = lmA.y * h;
        const bx = (1 - lmB.x) * w;
        const by = lmB.y * h;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.strokeStyle = handColor + '80';
        ctx.lineWidth = 2.5;
        ctx.lineCap = 'round';
        ctx.stroke();
      }

      // Draw joint dots
      landmarks.forEach((lm, idx) => {
        const x = (1 - lm.x) * w;
        const y = lm.y * h;
        const isTip = FINGER_TIPS.includes(idx);
        const isWrist = idx === WRIST_IDX;

        if (isTip) {
          ctx.beginPath();
          ctx.arc(x, y, 9, 0, Math.PI * 2);
          ctx.strokeStyle = handColor;
          ctx.lineWidth = 1.5;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.fillStyle = tipColor;
          ctx.shadowColor = handColor;
          ctx.shadowBlur = 10;
          ctx.fill();
          ctx.shadowBlur = 0;
        } else if (isWrist) {
          ctx.beginPath();
          ctx.arc(x, y, 6, 0, Math.PI * 2);
          ctx.strokeStyle = handColor;
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fillStyle = handColor;
          ctx.shadowColor = handColor;
          ctx.shadowBlur = 8;
          ctx.fill();
          ctx.shadowBlur = 0;
        } else {
          ctx.beginPath();
          ctx.arc(x, y, 3.5, 0, Math.PI * 2);
          ctx.fillStyle = handColor;
          ctx.shadowColor = handColor;
          ctx.shadowBlur = 5;
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      });
    });
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    activeRef.current = true;
    onStatus?.('starting');

    const initLandmarker = async () => {
      try {
        const { FilesetResolver, HandLandmarker } = await import('@mediapipe/tasks-vision');

        const vision = await FilesetResolver.forVisionTasks(
          `${import.meta.env.BASE_URL}mediapipe/wasm` // vendored: no CDN at runtime
        );

        const handLandmarker = await HandLandmarker.createFromOptions(vision, {
          baseOptions: {
            modelAssetPath: `${import.meta.env.BASE_URL}mediapipe/hand_landmarker.task`, // vendored, hash-pinned
            delegate: 'GPU',
          },
          numHands: 4, // the operator's two plus others in view: operatorLock keeps the operator's
          runningMode: 'VIDEO',
          minHandDetectionConfidence: 0.6,
          minHandPresenceConfidence: 0.6,
          minTrackingConfidence: 0.5,
        });

        if (!activeRef.current) { handLandmarker.close(); return; }
        landmarkerRef.current = handLandmarker;

        // Get webcam stream
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        // the page may have been left while the permission prompt was open: never keep the camera
        if (!activeRef.current) { stream.getTracks().forEach((t) => t.stop()); return; }
        video.srcObject = stream;
        await video.play();
        onStatus?.('on');

        // Detection loop
        const detect = () => {
          if (!activeRef.current) return;

          if (video.currentTime !== lastVideoTimeRef.current && video.readyState >= 2) {
            lastVideoTimeRef.current = video.currentTime;

            try {
              const results = handLandmarker.detectForVideo(video, performance.now());
              const w = canvas.width;
              const h = canvas.height;

              const all: NormalizedLandmark[][] = results.landmarks ?? [];
              const handedness: string[] = results.handedness
                ? results.handedness.map((h: { categoryName?: string; displayName?: string }[]) => h[0]?.categoryName || h[0]?.displayName || '')
                : [];
              const lock = lockStep(lockRef.current, all.map((lms, i) => ({ lms, label: handedness[i] })), performance.now());
              lockRef.current = lock.state;
              if (lock.ignored !== ignoredRef.current) { ignoredRef.current = lock.ignored; onIgnored?.(lock.ignored); }
              if (lock.keep.length > 0) {
                drawHands(ctx, all, w, h, lock.keep);
                onHandsUpdate(lock.keep.map((k) => all[k]), lock.keep.map((k) => handedness[k]));
              } else {
                if (all.length > 0) drawHands(ctx, all, w, h, []); else ctx.clearRect(0, 0, w, h);
                onHandsUpdate(null);
              }
            } catch (e) {
              // Detection error — skip this frame
            }
          }

          animFrameRef.current = requestAnimationFrame(detect);
        };

        animFrameRef.current = requestAnimationFrame(detect);
      } catch (err) {
        const denied = err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
        if (activeRef.current) onStatus?.(denied ? 'blocked' : 'unavailable');
      }
    };

    initLandmarker();

    return () => {
      activeRef.current = false;
      cancelAnimationFrame(animFrameRef.current);
      if (landmarkerRef.current) {
        landmarkerRef.current.close();
        landmarkerRef.current = null;
      }
      const stream = video.srcObject as MediaStream | null;
      stream?.getTracks().forEach(t => t.stop());
      video.srcObject = null;
      onHandsUpdate(null);
    };
  }, [drawHands, onHandsUpdate, onStatus, onIgnored]);

  return (
    <>
      <video
        ref={videoRef}
        style={{ display: 'none' }}
        playsInline
        muted
      />
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
          zIndex: 8,
        }}
      />
    </>
  );
}

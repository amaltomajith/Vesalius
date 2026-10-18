import { Suspense, useRef } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import AnatomicalModel from './AnatomicalModel';
import HallStage from './HallStage';
import type { GestureState } from '../hooks/useGestureControl';
import type { Pt } from '../hooks/gestureEngine';

interface HologramCanvasProps {
  gestureState: GestureState;
  selectedMesh: string | null;
  setSelectedMesh: (mesh: string | null) => void;
  onPick: (target: string | null) => void;
  removal: { offset: Pt; live: boolean };
  flyDir: Pt;
  onAiSnapshot: (capture: () => string) => void;
  setHoveredMesh: (mesh: string | null) => void;
  partsRemoved: boolean;
  faceViewer: boolean;
}

/** shown while the canvas itself starts: one calm line, the hall already behind it */
function CanvasLoadingFallback() {
  return (
    <div className="glb-loading-overlay">
      <p className="glb-loading-text">Loading anatomy</p>
    </div>
  );
}

export default function HologramCanvas({
  gestureState,
  selectedMesh,
  setSelectedMesh,
  onPick,
  removal,
  flyDir,
  onAiSnapshot,
  setHoveredMesh,
  partsRemoved,
  faceViewer,
}: HologramCanvasProps) {
  const { modelPosition, modelRotationX, modelRotationY, modelScale, handsVisible, activeGesture } = gestureState;
  // Block orbit-rotate only while a hand is actually steering the model (a drag or a zoom).
  const gestureIsLive = handsVisible && gestureState.grabbing;
  // a click on empty space steps the selection back; the click that ends an orbit drag does not
  const downAt = useRef<{ x: number; y: number } | null>(null);

  return (
    <div
      style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0, zIndex: 5 }}
      onPointerDown={(e) => { downAt.current = { x: e.clientX, y: e.clientY }; }}
    >
      <Suspense fallback={<CanvasLoadingFallback />}>
        <Canvas
          camera={{ position: [0, 0, 4.5], fov: 50, near: 0.005, far: 100 }}
          gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}
          onPointerMissed={(e) => {
            const d = downAt.current;
            if (e.button === 0 && (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5)) onPick(null);
          }}
        >
          {/* the hall (background, spotlight) is CSS on .room behind this transparent canvas;
              the lights, environment and stage are the landing hall's (HallStage) */}
          <HallStage />

          {/* OrbitControls: zoom/pan always enabled; orbit-rotate blocked only during live hand gesture */}
          <OrbitControls
            enableZoom={true}
            enableRotate={!gestureIsLive}
            enablePan={true}
            minDistance={0.005}
            maxDistance={14}
            makeDefault
          />

          <AnatomicalModel
            modelPosition={modelPosition}
            modelRotationX={modelRotationX}
            modelRotationY={modelRotationY}
            modelScale={modelScale}
            handsVisible={handsVisible}
            activeGesture={activeGesture}
            gestureState={gestureState}
            selectedMesh={selectedMesh}
            setSelectedMesh={setSelectedMesh}
            onPick={onPick}
            removal={removal}
            flyDir={flyDir}
            onAiSnapshot={onAiSnapshot}
            setHoveredMesh={setHoveredMesh}
            partsRemoved={partsRemoved}
            faceViewer={faceViewer}
          />
        </Canvas>
      </Suspense>
    </div>
  );
}

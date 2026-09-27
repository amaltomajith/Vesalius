import { Suspense } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, Grid } from '@react-three/drei';
import AnatomicalModel from './AnatomicalModel';
import type { GestureState } from '../hooks/useGestureControl';

interface HologramCanvasProps {
  gestureState: GestureState;
  selectedMesh: string | null;
  setSelectedMesh: (mesh: string | null) => void;
  onAiSnapshot: (base64Image: string) => void;
  setHoveredMesh: (mesh: string | null) => void;
  partsRemoved: boolean;
  faceViewer: boolean;
}

/** HUD-styled loading indicator shown while GLBs are streaming in */
function CanvasLoadingFallback() {
  return (
    <div className="glb-loading-overlay">
      <div className="glb-loading-ring" />
      <div className="glb-loading-text">LOADING ANATOMY</div>
    </div>
  );
}

export default function HologramCanvas({
  gestureState,
  selectedMesh,
  setSelectedMesh,
  onAiSnapshot,
  setHoveredMesh,
  partsRemoved,
  faceViewer,
}: HologramCanvasProps) {
  const { modelPosition, modelRotationX, modelRotationY, modelScale, handsVisible, activeGesture, isFrozen } = gestureState;
  // Block orbit-rotate only when a live hand gesture is actively steering the model.
  // Freeze mode does NOT block orbit controls — user can still scroll-zoom and pan.
  const gestureIsLive = handsVisible && !isFrozen && activeGesture !== 'NONE';

  return (
    <div style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0, zIndex: 5 }}>
      <Suspense fallback={<CanvasLoadingFallback />}>
        <Canvas
          camera={{ position: [0, 0, 4.5], fov: 50, near: 0.005, far: 100 }}
          gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}
        >
          {/* Background */}
          <color attach="background" args={['#0a2a21']} />

          {/* Anatomically-accurate neutral lighting — kept moderate so pigment colours show */}
          {/* Minimal ambient so shadows don't go pitch-black */}
          <ambientLight intensity={0.15} color="#ffffff" />
          {/* Key light: bright neutral-warm from upper-right front */}
          <directionalLight position={[4, 5, 5]}   intensity={1.1} color="#fff8f0" />
          {/* Fill: slightly cool from the left to reveal form */}
          <directionalLight position={[-3, 2, 3]}  intensity={0.45} color="#f0f4ff" />
          {/* Rim: very soft warm backlight for separation */}
          <directionalLight position={[0, -2, -5]} intensity={0.25} color="#ffe8d0" />
          {/* Under-fill: stop the bottom going completely dark */}
          <pointLight position={[0, -2, 2]} intensity={0.35} distance={8} color="#fff8f0" />

          {/* OrbitControls: zoom/pan always enabled; orbit-rotate blocked only during live hand gesture */}
          <OrbitControls
            enableZoom={true}
            enableRotate={!gestureIsLive}
            enablePan={true}
            minDistance={0.005}
            maxDistance={14}
            makeDefault
          />

          {/* Grid Floor */}
          <Grid
            position={[0, -1.2, 0]}
            args={[10, 10]}
            cellSize={0.4}
            cellThickness={0.4}
            cellColor="#164a3b"
            sectionSize={2.0}
            sectionThickness={0.8}
            sectionColor="#1f5e4b"
            fadeDistance={6}
            infiniteGrid={false}
          />

          <AnatomicalModel
            modelPosition={modelPosition}
            modelRotationX={modelRotationX}
            modelRotationY={modelRotationY}
            modelScale={modelScale}
            handsVisible={handsVisible}
            isFrozen={isFrozen}
            activeGesture={activeGesture}
            gestureState={gestureState}
            selectedMesh={selectedMesh}
            setSelectedMesh={setSelectedMesh}
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

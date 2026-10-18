import { useEffect, useMemo, useRef } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import { Environment } from '@react-three/drei';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { stageFollow } from '../lib/stageFollow';

/**
 * The room as the landing's hall (src/api/static/hall-scene.js), the 2026-10-18 redesign: a
 * studio light rig, a local environment map for real specular (three's RoomEnvironment through
 * PMREM: nothing is fetched), and a stage for the anatomy (a dark stone pedestal, a soft shadow on
 * it, a thin mint orbit ring behind with its bead). The hall's colour and spotlight are CSS on
 * `.room`, behind a transparent canvas. The stage goes with the body (lib/stageFollow.ts): it
 * takes the model's hand pan and zoom, never its turn, and fades out while the body is tipped.
 *
 * The shadow is a static radial texture, not drei's ContactShadows, which re-renders the whole
 * scene every frame (the atlas has ~3,700 meshes).
 */

const FEET = -1.06;      // the auto-fitted model is ~2 units tall, centred: its feet sit near -1
const MINT = '#c9f2df';
// the stage is scenery: never hit by a pick (a ray through it must reach the anatomy, or nothing)
const noRay = () => null;
const REDUCED = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

function softShadow(): THREE.CanvasTexture {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d')!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(0,0,0,0.62)');
  grad.addColorStop(0.55, 'rgba(0,0,0,0.28)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

export default function HallStage() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const envMap = useMemo(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const tex = pmrem.fromScene(room, 0.04).texture;
    room.dispose();
    pmrem.dispose();
    return tex;
  }, [gl]);
  const shadow = useMemo(() => softShadow(), []);
  useEffect(() => () => { envMap.dispose(); shadow.dispose(); }, [envMap, shadow]);

  // the bead on the orbit ring, as on the landing: a slow turn (still under reduced motion)
  const bead = useRef<THREE.Group>(null);
  // the stage follows the model's group (AnatomicalModel, named "anatomy-model"); its materials
  // fade together, each from its own opacity
  const stage = useRef<THREE.Group>(null);
  const model = useRef<THREE.Object3D | null>(null);
  const fades = useRef<{ mat: THREE.Material; base: number }[] | null>(null);
  const shown = useRef(1);
  useFrame((_, dt) => {
    if (bead.current && !REDUCED) bead.current.rotation.z -= dt * 0.18;
    const st = stage.current;
    if (!st) return;
    if (!model.current || !model.current.parent) model.current = scene.getObjectByName('anatomy-model') ?? null;
    const m = model.current;
    if (!m) return;
    const f = stageFollow({ x: m.position.x, y: m.position.y, scale: m.scale.x, fit: m.userData.fit ?? m.scale.x, rotX: m.rotation.x });
    st.position.set(f.x, f.y, 0);
    st.scale.setScalar(f.scale);
    if (Math.abs(f.opacity - shown.current) > 0.005) {
      if (!fades.current) {
        const list: { mat: THREE.Material; base: number }[] = [];
        st.traverse((o) => {
          const mat = (o as THREE.Mesh).material as THREE.Material | undefined;
          if (mat) { mat.transparent = true; list.push({ mat, base: mat.opacity }); }
        });
        fades.current = list;
      }
      fades.current.forEach(({ mat, base }) => { mat.opacity = base * f.opacity; });
      st.visible = f.opacity > 0.01;
      shown.current = f.opacity;
    }
  });

  return (
    <>
      <Environment map={envMap} environmentIntensity={0.55} />

      {/* the studio rig: cool sky / near-black ground, a warm key from the front, a mint rim
          from behind for separation against the hall, a soft fill */}
      <hemisphereLight args={['#9fc4b6', '#030605', 0.5]} />
      <spotLight position={[2.4, 4.6, 4.2]} angle={0.5} penumbra={0.85} decay={2} intensity={95} color="#fff6ea" />
      <spotLight position={[-1.6, 2.8, -4]} angle={0.6} penumbra={0.9} decay={2} intensity={55} color="#7fe3b8" />
      <directionalLight position={[-3, 1.5, 3]} intensity={0.35} color="#bfd8cf" />

      <group ref={stage}>
      {/* the pedestal */}
      <group position={[0, FEET, 0]}>
        {/* a low matte plinth and its step: stone, not chrome */}
        <mesh raycast={noRay} position={[0, -0.06, 0]}>
          <cylinderGeometry args={[0.6, 0.64, 0.12, 96]} />
          <meshStandardMaterial color="#111a17" roughness={0.7} metalness={0} envMapIntensity={0.35} />
        </mesh>
        <mesh raycast={noRay} position={[0, -0.16, 0]}>
          <cylinderGeometry args={[0.86, 0.9, 0.08, 96]} />
          <meshStandardMaterial color="#0b1311" roughness={0.75} metalness={0} envMapIntensity={0.3} />
        </mesh>
        {/* the soft shadow the model casts on the stone */}
        <mesh raycast={noRay} rotation-x={-Math.PI / 2} position={[0, 0.003, 0]}>
          <planeGeometry args={[1.1, 1.1]} />
          <meshBasicMaterial map={shadow} transparent depthWrite={false} />
        </mesh>
        {/* a hairline of mint around the step, as the landing's floor lines */}
        <mesh raycast={noRay} rotation-x={-Math.PI / 2} position={[0, -0.119, 0]}>
          <ringGeometry args={[0.875, 0.885, 160]} />
          <meshBasicMaterial color={MINT} transparent opacity={0.28} depthWrite={false} />
        </mesh>
      </group>

      {/* the orbit ring behind the subject, with its bead */}
      <group position={[0, 0.02, -0.9]}>
        <mesh raycast={noRay}>
          <ringGeometry args={[1.62, 1.632, 200]} />
          <meshBasicMaterial color={MINT} transparent opacity={0.2} depthWrite={false} />
        </mesh>
        <group ref={bead} rotation-z={0.6}>
          <mesh raycast={noRay} position={[1.626, 0, 0]}>
            <sphereGeometry args={[0.035, 20, 14]} />
            <meshBasicMaterial color={MINT} transparent opacity={0.85} />
          </mesh>
        </group>
      </group>
      </group>
    </>
  );
}

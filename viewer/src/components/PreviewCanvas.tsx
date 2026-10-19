import { Suspense, useEffect, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { Bounds, Center, OrbitControls, useGLTF } from '@react-three/drei';
import { DRACO_DECODER } from '../lib/runtime';
import * as THREE from 'three';

function Model({ url }: { url: string }) {
  const { scene } = useGLTF(url, DRACO_DECODER, false);
  // a private copy with its own materials (VIEWER-CONSTRAINTS: never mutate the cached scene)
  const clone = useMemo(() => {
    const c = scene.clone(true);
    c.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.material = new THREE.MeshStandardMaterial({ color: '#ede6d6', roughness: 0.55, metalness: 0 });
      }
    });
    return c;
  }, [scene]);
  useEffect(() => () => {
    clone.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) (m.material as THREE.Material).dispose();
    });
    useGLTF.clear(url);
  }, [clone, url]);
  // pipeline output: GEOMETRY.md exports posterior +Z, so turn half a turn to face the camera
  return <primitive object={clone} rotation={[0, Math.PI, 0]} />;
}

/** A small, auto-rotating look at a published model, inside the dashboard. The hologram (full
 *  screen, hands) is one click away. */
export default function PreviewCanvas({ url }: { url: string }) {
  return (
    <div className="preview-canvas" data-testid="preview">
      <Canvas camera={{ position: [0, 0, 3], fov: 40 }} dpr={[1, 1.75]}>
        <color attach="background" args={['#0f3b2f']} />
        <hemisphereLight args={['#f3f5f1', '#0f3b2f', 0.9]} />
        <directionalLight position={[3, 4, 5]} intensity={1.6} />
        <directionalLight position={[-3, 1, 2]} intensity={0.4} color="#c9f2df" />
        <Suspense fallback={null}>
          <Bounds fit clip observe margin={1.15}>
            <Center>
              <Model url={url} />
            </Center>
          </Bounds>
        </Suspense>
        <OrbitControls autoRotate autoRotateSpeed={0.8} enablePan={false} makeDefault />
      </Canvas>
    </div>
  );
}

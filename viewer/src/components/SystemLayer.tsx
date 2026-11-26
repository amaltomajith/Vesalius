import { useEffect, useRef } from 'react';
import { useGLTF } from '@react-three/drei';
import * as THREE from 'three';

interface SystemLayerProps {
  /** Presigned-redirect (pipeline) or bundled (demo) URL of one group GLB. */
  url: string;
  color: string;
  emissive: string;
  visible: boolean;
  onLoaded?: (box: THREE.Box3) => void;
  /** node names kept loaded but never drawn (atlas landmark / leader-line markers) */
  hidden?: ReadonlySet<string>;
}

const NONE: ReadonlySet<string> = new Set();

function inHidden(obj: THREE.Object3D, root: THREE.Object3D, hidden: ReadonlySet<string>): boolean {
  let o: THREE.Object3D | null = obj;
  while (o && o !== root) {
    if (hidden.has(o.name)) return true;
    o = o.parent;
  }
  return false;
}

/**
 * Loads one GLB system and overrides every mesh with a holographic
 * MeshStandardMaterial in the system's assigned colour.
 *
 * Key design decisions:
 * - scene.clone(true) is stored in a stable ref so the primitive never
 *   re-mounts (prevents reload flicker on visibility toggle).
 * - Material application runs in a useEffect with `scene` as a dep so it
 *   fires AFTER the GLB is fully parsed — the clone's own materials have
 *   already settled by then, so we safely win the override race.
 * - Each mesh gets its OWN MeshStandardMaterial instance (no sharing) to
 *   prevent the cross-mesh flicker that a single shared material causes.
 * - Visibility is toggled by traversing meshes and setting child.visible,
 *   never by unmounting, so the GPU buffers stay warm.
 */
export default function SystemLayer({ url, color, emissive, visible, onLoaded, hidden = NONE }: SystemLayerProps) {
  // Draco/meshopt off: pipeline GLBs are uncompressed (Draco deferred), and drei's default
  // Draco decoder would come from a CDN.
  const { scene } = useGLTF(url, false, false);

  // Stable clone — created once per scene identity, never replaced.
  const cloneRef = useRef<THREE.Group | null>(null);
  const reportedRef = useRef(false);
  const materialsRef = useRef<THREE.MeshStandardMaterial[]>([]);

  // Initialise / replace the clone whenever the underlying scene changes.
  if (!cloneRef.current) {
    cloneRef.current = scene.clone(true);
  }

  // ── Material application ───────────────────────────────────────────────
  // Runs after the GLB is fully loaded (scene reference changes).
  // Creates a fresh per-mesh material so there is zero shared-instance flicker.
  useEffect(() => {
    const clone = cloneRef.current;
    if (!clone) return;

    // Dispose any previously created materials to avoid GPU leaks on hot-reload
    materialsRef.current.forEach((m) => m.dispose());
    materialsRef.current = [];

    clone.traverse((child) => {
      if (!(child as THREE.Mesh).isMesh) return;
      const mesh = child as THREE.Mesh;

      // the room redesign (2026-10-18): a lit anatomical surface (the hall's environment map gives
      // it real specular) with only a whisper of its own colour as glow; selection raises it
      const mat = new THREE.MeshStandardMaterial({
        color:            new THREE.Color(color),
        emissive:         new THREE.Color(emissive),
        emissiveIntensity: 0.04,
        roughness:        0.5,
        metalness:        0,
        envMapIntensity:  0.9,
        transparent:      true,
        opacity:          0.75,
        depthWrite:       false,
      });
      mat.needsUpdate = true;

      mesh.material        = mat;
      mesh.castShadow      = false;
      mesh.receiveShadow   = false;

      materialsRef.current.push(mat);
    });

    // Report bounding box once per scene identity, in the layer's OWN frame: a world-space box
    // would include whatever gesture rotation/scale the model had when this group finished
    // loading, and groups now load at different times (on first toggle).
    if (!reportedRef.current && onLoaded) {
      clone.updateWorldMatrix(true, true);
      const toLocal = clone.matrixWorld.clone().invert();
      const rel = new THREE.Matrix4();
      const box = new THREE.Box3();
      clone.traverse((child) => {
        const mesh = child as THREE.Mesh;
        if (!mesh.isMesh || inHidden(mesh, clone, hidden)) return;
        if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
        rel.multiplyMatrices(toLocal, mesh.matrixWorld);
        box.union(mesh.geometry.boundingBox!.clone().applyMatrix4(rel));
      });
      if (!box.isEmpty()) {
        reportedRef.current = true;
        onLoaded(box);
      }
    }

    return () => {
      // Cleanup on unmount or before next run — materials are disposed above
      // on the next run, or here on full unmount.
      materialsRef.current.forEach((m) => m.dispose());
      materialsRef.current = [];
    };
  // Re-run whenever the parsed scene object itself changes (i.e. on first load).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene]);


  // ── Study switch ─────────────────────────────────────────────────────
  // Pipeline GLBs are per-patient and never reused across studies, so on unmount the
  // geometries are freed and the loader cache entry evicted (VIEWER-CONSTRAINTS "Loading
  // behaviour that changes"). The clone shares geometry with the cached scene, so both go.
  useEffect(() => {
    return () => {
      scene.traverse((child) => {
        if ((child as THREE.Mesh).isMesh) (child as THREE.Mesh).geometry.dispose();
      });
      useGLTF.clear(url);
    };
  }, [scene, url]);

  // ── Visibility ────────────────────────────────────────────────────────
  // Toggle mesh visibility without unmounting — keeps GPU buffers warm and
  // prevents the reload-flicker that null-return / re-mount causes.
  useEffect(() => {
    const clone = cloneRef.current;
    if (!clone) return;
    clone.traverse((child) => {
      if (hidden.has(child.name)) {
        child.visible = false; // stays loaded (GPU buffers warm), never drawn
      } else if ((child as THREE.Mesh).isMesh) {
        child.visible = visible;
      }
    });
    // Also hide/show the root group so its bounding contributions stay correct
    clone.visible = visible;
  }, [visible, hidden]);

  // Always render the primitive — visibility is managed inside the group.
  return (
    <primitive
      ref={cloneRef}
      object={cloneRef.current}
    />
  );
}

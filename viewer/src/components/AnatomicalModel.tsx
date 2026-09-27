import { Suspense, useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import * as THREE from 'three';
import SystemLayer from './SystemLayer';
import { useLayerStore } from '../store/layerStore';
import { useStudyStore } from '../store/studyStore';
import { getFingerRay } from '../utils/raycastUtils';
import type { GestureState } from '../hooks/useGestureControl';

// refs/GEOMETRY.md exports Posterior -> +Z (glTF "forward"), so a pipeline study arrives with
// its back to a camera on +Z. The model is turned half a turn about the vertical axis so the
// anterior faces the viewer. A rotation (det +1), never a mirror: left stays left. The atlas
// already faces +Z, so it is not turned.
const HALF_TURN = new THREE.Matrix4().makeRotationY(Math.PI);
const NO_TURN = new THREE.Matrix4();

/** The structure an object belongs to: itself or its nearest ancestor that the scene's manifest
 *  names. A multi-primitive glTF mesh loads as a Group of child meshes ("<name>_0", ...), so the
 *  hit child is not the structure; the named Group is. */
function rootOf(obj: THREE.Object3D | null, names: Record<string, unknown>, stop: THREE.Object3D): THREE.Object3D | null {
  let o = obj;
  while (o && o !== stop) {
    if (o.name && o.name in names) return o;
    o = o.parent;
  }
  return obj;
}

/** uuid-based membership (VIEWER-CONSTRAINTS: animation state keys on uuid, never on name). */
function within(mesh: THREE.Object3D, root: THREE.Object3D | null): boolean {
  if (!root) return false;
  let o: THREE.Object3D | null = mesh;
  while (o) {
    if (o.uuid === root.uuid) return true;
    o = o.parent;
  }
  return false;
}

function visibleChain(obj: THREE.Object3D): boolean {
  let o: THREE.Object3D | null = obj;
  while (o) { if (!o.visible) return false; o = o.parent; }
  return true;
}

interface AnatomicalModelProps {
  modelPosition: { x: number; y: number };
  modelRotationX: number;
  modelRotationY: number;
  modelScale: number;
  handsVisible: boolean;
  isFrozen: boolean;
  activeGesture: string;
  // selection & snapshot props
  gestureState: GestureState;
  selectedMesh: string | null;
  setSelectedMesh: (mesh: string | null) => void;
  onAiSnapshot: (base64Image: string) => void;
  setHoveredMesh: (mesh: string | null) => void;
  partsRemoved: boolean;
  /** turn the model half a turn to face the camera (pipeline studies); false for the atlas */
  faceViewer: boolean;
}

export default function AnatomicalModel({
  modelPosition,
  modelRotationX,
  modelRotationY,
  modelScale,
  handsVisible,
  isFrozen,
  activeGesture,
  gestureState,
  selectedMesh,
  setSelectedMesh,
  onAiSnapshot,
  setHoveredMesh,
  partsRemoved,
  faceViewer,
}: AnatomicalModelProps) {
  const groupRef = useRef<THREE.Group>(null);
  const turn = faceViewer ? HALF_TURN : NO_TURN;
  const structures = useStudyStore((s) => s.structures);
  const structuresRef = useRef(structures);
  structuresRef.current = structures;
  // Z-Anatomy landmark / leader-line markers: annotation, not anatomy. Loaded, never drawn.
  const hidden = useMemo(
    () => new Set(Object.values(structures).filter((s) => s.landmark).map((s) => s.node)),
    [structures],
  );
  const studyKey   = useLayerStore((s) => s.studyKey);
  const order      = useLayerStore((s) => s.order);
  const groups     = useLayerStore((s) => s.groups);
  const markLoaded = useLayerStore((s) => s.markLoaded);

  // Bounding-box state for auto-centering & scaling
  const [centerOffset, setCenterOffset] = useState<THREE.Vector3>(new THREE.Vector3(0, 0, 0));
  const [autoScale,    setAutoScale]    = useState(1);

  // Track the last gesture scale so we can restore it correctly when the hand
  // leaves frame (avoids snapping back to bare autoScale).
  const gestureScaleRef = useRef(1.0);

  // Accumulate bounding boxes from each loaded system
  const boxMap = useRef<Map<string, THREE.Box3>>(new Map());

  // Raycasting and Snapshot Cooldown Refs
  const { camera, gl } = useThree();
  const aspect = useThree((st) => st.size.width / Math.max(1, st.size.height));
  const hoveredMeshRef = useRef<THREE.Object3D | null>(null); // a structure root
  const snapshotCooldownRef = useRef(false);

  // Edge-trigger refs: fire selection/dismiss only on gesture onset, not every frame
  const prevVictoryRef = useRef(false);   // was victory ✌️ active last frame?
  const prevFistRef    = useRef(false);   // was left fist active last frame?
  const dismissCooldownRef = useRef(false); // brief cooldown after dismiss to stop immediate re-select

  // The selected structure's root object (a Mesh, or a Group for multi-primitive meshes)
  const selectedMeshRef = useRef<THREE.Object3D | null>(null);
  const allMeshesRef = useRef<THREE.Mesh[]>([]);

  // Sync selectedMeshRef with selectedMesh prop using THREE reference
  useEffect(() => {
    if (!selectedMesh || !groupRef.current) {
      selectedMeshRef.current = null;
      return;
    }
    selectedMeshRef.current = groupRef.current.getObjectByName(selectedMesh) ?? null;
  }, [selectedMesh]);

  const recomputeBounds = useCallback(() => {
    const combined = new THREE.Box3();
    boxMap.current.forEach((box) => {
      if (!box.isEmpty()) combined.union(box);
    });
    if (combined.isEmpty()) return;

    const size   = new THREE.Vector3();
    const center = new THREE.Vector3();
    combined.getSize(size);
    combined.getCenter(center);

    // Target height ≈ 2 units in scene space, but never wider than ~80% of the view: a patient
    // study is often wider than tall (abdomen, chest), which the fixed atlas never was, and on a
    // portrait screen height-only fitting overflows the sides.
    const targetHeight = 2.0;
    const rawHeight    = size.y || 1;
    const maxWidth     = 3.3 * aspect;
    const scale        = Math.min(targetHeight / rawHeight, maxWidth / (size.x || 1));

    setCenterOffset(center.clone().negate());
    setAutoScale(scale);
  }, [aspect]);

  useEffect(() => { recomputeBounds(); }, [recomputeBounds]);

  const handleLoaded = useCallback((key: string) => (box: THREE.Box3) => {
    boxMap.current.set(key, box.clone().applyMatrix4(turn));
    markLoaded(key);
    recomputeBounds();
  }, [recomputeBounds, markLoaded, turn]);

  // A new study starts from empty bounds: patient anatomy varies in extent far more than the
  // fixed atlas did (VIEWER-CONSTRAINTS "Auto-centring").
  useEffect(() => {
    boxMap.current.clear();
    setSelectedMesh(null);
  }, [studyKey, setSelectedMesh]);

  // Mouse path for selection (additive; the gesture path above is unchanged). A click that
  // ended an orbit drag is ignored, and the first *visible* mesh under the pointer wins.
  const handleClick = useCallback((e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4) return;
    const hit = e.intersections.find((h) => (h.object as THREE.Mesh).isMesh && visibleChain(h.object));
    if (!hit || !groupRef.current) return;
    e.stopPropagation();
    const root = rootOf(hit.object, structuresRef.current, groupRef.current);
    if (root) setSelectedMesh(root.name);
  }, [setSelectedMesh]);

  // Frame loop for transforms, raycasting, animations, and snapshot gesture detection
  useFrame((state) => {
    if (!groupRef.current) return;
    const g = groupRef.current;

    // Collect all meshes dynamically in useFrame
    const currentMeshes: THREE.Mesh[] = [];
    g.traverse((child: any) => {
      if (child.isMesh) {
        currentMeshes.push(child as THREE.Mesh);
      }
    });

    if (allMeshesRef.current.length !== currentMeshes.length) {
      currentMeshes.forEach((mesh) => {
        if (mesh.material) {
          const mat = mesh.material as THREE.MeshStandardMaterial;
          mat.transparent = true;
        }
      });
      allMeshesRef.current = currentMeshes;
    }

    // Sync selectedMeshRef fallback
    if (selectedMesh && (!selectedMeshRef.current || selectedMeshRef.current.name !== selectedMesh)) {
      selectedMeshRef.current = g.getObjectByName(selectedMesh) ?? null;
    } else if (!selectedMesh) {
      selectedMeshRef.current = null;
    }

    // ── Standard Gesture Transforms ──────────────────────────────────────
    gestureScaleRef.current = modelScale;

    const lerpPosRot = handsVisible && !isFrozen;
    const lerpPos = lerpPosRot || (handsVisible && activeGesture === 'ZOOM');
    const lerpScale = handsVisible;

    g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, modelRotationY,  lerpPosRot ? 0.12 : 0);
    g.rotation.x = THREE.MathUtils.lerp(g.rotation.x, modelRotationX,  lerpPosRot ? 0.12 : 0);
    g.position.x = THREE.MathUtils.lerp(g.position.x, modelPosition.x, lerpPos ? 0.22 : 0);
    g.position.y = THREE.MathUtils.lerp(g.position.y, modelPosition.y, lerpPos ? 0.22 : 0);
    g.scale.setScalar(THREE.MathUtils.lerp(g.scale.x,  modelScale * autoScale, lerpScale ? 0.18 : 0));

    // ── Raycasting & Part Selection (Freeze Mode Only) ───────────────────
    let currentHovered: THREE.Object3D | null = null;

    if (isFrozen && gestureState.leftIndexFingerTip) {
      const raycaster = getFingerRay(
        gestureState.leftIndexFingerTip.x,
        gestureState.leftIndexFingerTip.y,
        camera
      );

      const intersects = raycaster.intersectObject(g, true);
      // first VISIBLE mesh: hidden layers stay loaded (never unmounted), so skip them
      const firstMesh = intersects.find((h) => (h.object as THREE.Mesh).isMesh && visibleChain(h.object));

      if (firstMesh) {
        currentHovered = rootOf(firstMesh.object, structuresRef.current, g);
      }
    }

    // Handle Hover Highlighting State
    if (currentHovered !== hoveredMeshRef.current) {
      hoveredMeshRef.current = currentHovered;
      setHoveredMesh(currentHovered ? currentHovered.name : null);
    }

    // ── Victory ✌️ to Select (primary, edge-triggered) ───────────────────
    const victoryNow   = gestureState.leftVictoryActive;
    const victoryOnset = victoryNow && !prevVictoryRef.current;
    prevVictoryRef.current = victoryNow;

    if (isFrozen && victoryOnset && hoveredMeshRef.current && !dismissCooldownRef.current) {
      setSelectedMesh(hoveredMeshRef.current.name);
    }

    // ── Dismiss panel on left fist (edge-triggered + cooldown) ───────────
    const fistNow = gestureState.leftFistActive;
    const fistOnset = fistNow && !prevFistRef.current;
    prevFistRef.current = fistNow;

    if (fistOnset && selectedMeshRef.current !== null) {
      setSelectedMesh(null);
      // Lock out re-select for 500 ms so the fist-release doesn't immediately
      // re-trigger the selection on the same gesture.
      dismissCooldownRef.current = true;
      setTimeout(() => { dismissCooldownRef.current = false; }, 500);
    }

    // ── Dynamic Mesh Animations (Scale, Opacity, and Glowing) ───────────
    const hasSelection = selectedMeshRef.current !== null;

    if (allMeshesRef.current.length > 0 && hasSelection) {
      allMeshesRef.current.forEach((mesh) => {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (!mat) return;

        // Ensure stable caching of original scale, position, and opacity
        if (mesh.userData.originalPosition === undefined) {
          mesh.userData.originalPosition = mesh.position.clone();
        }
        if (mesh.userData.originalScale === undefined) {
          mesh.userData.originalScale = mesh.scale.clone();
        }
        if (mesh.userData.originalOpacity === undefined) {
          mesh.userData.originalOpacity = mat.opacity !== undefined ? mat.opacity : 0.75;
        }

        const isSelected = within(mesh, selectedMeshRef.current);

        // 1. Calculate Target Position & Opacity
        let targetX = mesh.userData.originalPosition.x;
        let targetY = mesh.userData.originalPosition.y;
        let targetOpacity = isSelected ? 1.0 : 0.15;

        if (isSelected) {
          targetOpacity = 1.0;
        } else if (partsRemoved) {
          // Slide away and completely remove all other parts
          targetX = mesh.userData.originalPosition.x + 4.0;
          targetOpacity = 0.0;
        }

        // Smoothly animate position & opacity
        mesh.position.x = THREE.MathUtils.lerp(mesh.position.x, targetX, 0.12);
        mesh.position.y = THREE.MathUtils.lerp(mesh.position.y, targetY, 0.12);

        mat.opacity = THREE.MathUtils.lerp(mat.opacity ?? 1.0, targetOpacity, 0.12);
        mat.transparent = true;

        // 2. Calculate Target Scale (selected renders at normal scale)
        const targetScaleFactor = 1.0;
        const targetScale = mesh.userData.originalScale.clone().multiplyScalar(targetScaleFactor);
        mesh.scale.x = THREE.MathUtils.lerp(mesh.scale.x, targetScale.x, 0.12);
        mesh.scale.y = THREE.MathUtils.lerp(mesh.scale.y, targetScale.y, 0.12);
        mesh.scale.z = THREE.MathUtils.lerp(mesh.scale.z, targetScale.z, 0.12);

        // 3. Calculate Target Emissive Intensity (pulsing glow)
        let targetEmissive = 0.3;
        if (isSelected) {
          const elapsed = state.clock.getElapsedTime();
          targetEmissive = 1.8 + Math.sin(elapsed * 10) * 0.4; // Strong pulsing selected glow (1.4 - 2.2)
        }

        mat.emissiveIntensity = THREE.MathUtils.lerp(
          mat.emissiveIntensity,
          targetEmissive,
          0.15
        );
      });
    } else {
      // Restore all to full opacity & original positions/scales
      allMeshesRef.current.forEach((mesh) => {
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (!mat) return;

        // Ensure stable caching of original scale, position, and opacity
        if (mesh.userData.originalPosition === undefined) {
          mesh.userData.originalPosition = mesh.position.clone();
        }
        if (mesh.userData.originalScale === undefined) {
          mesh.userData.originalScale = mesh.scale.clone();
        }
        if (mesh.userData.originalOpacity === undefined) {
          mesh.userData.originalOpacity = mat.opacity !== undefined ? mat.opacity : 0.75;
        }

        const isHovered = within(mesh, hoveredMeshRef.current);

        let targetX = mesh.userData.originalPosition.x;
        let targetY = mesh.userData.originalPosition.y;
        let targetOpacity = 1.0;

        // Smoothly animate position & opacity
        mesh.position.x = THREE.MathUtils.lerp(mesh.position.x, targetX, 0.12);
        mesh.position.y = THREE.MathUtils.lerp(mesh.position.y, targetY, 0.12);

        mat.opacity = THREE.MathUtils.lerp(mat.opacity ?? 1.0, targetOpacity, 0.12);
        mat.transparent = true;

        // 2. Calculate Target Scale (hovered pops out slightly, others normal scale)
        let targetScaleFactor = isHovered ? 1.05 : 1.0;
        const targetScale = mesh.userData.originalScale.clone().multiplyScalar(targetScaleFactor);
        mesh.scale.x = THREE.MathUtils.lerp(mesh.scale.x, targetScale.x, 0.12);
        mesh.scale.y = THREE.MathUtils.lerp(mesh.scale.y, targetScale.y, 0.12);
        mesh.scale.z = THREE.MathUtils.lerp(mesh.scale.z, targetScale.z, 0.12);

        // 3. Calculate Target Emissive Intensity (pulsing glow)
        let targetEmissive = 0.3;
        if (isHovered) {
          const elapsed = state.clock.getElapsedTime();
          targetEmissive = 1.5 + Math.sin(elapsed * 8) * 0.5; // Hover pulsing glow (1.0 - 2.0)
        }

        mat.emissiveIntensity = THREE.MathUtils.lerp(
          mat.emissiveIntensity,
          targetEmissive,
          0.15
        );
      });
    }

    // ── AI Snapshot Gesture Detection (Both Index Fingers Pointing) ──────
    if (gestureState.leftIndexFingerTip && gestureState.rightIndexFingerTip) {
      const dx = Math.abs(gestureState.leftIndexFingerTip.x - gestureState.rightIndexFingerTip.x);
      const dy = Math.abs(gestureState.leftIndexFingerTip.y - gestureState.rightIndexFingerTip.y);

      // Pointing towards each other: close horizontally (< 0.15) and vertically (< 0.08)
      if (dx < 0.15 && dy < 0.08) {
        if (!snapshotCooldownRef.current) {
          snapshotCooldownRef.current = true;
          try {
            // Capture immediate WebGL framebuffer content
            const dataUrl = gl.domElement.toDataURL('image/png');
            onAiSnapshot(dataUrl);
          } catch (err) {
            console.error('[AnatomicalModel] Framebuffer snapshot capture failed:', err);
          }
        }
      } else {
        // Reset once index fingers separate
        snapshotCooldownRef.current = false;
      }
    } else {
      snapshotCooldownRef.current = false;
    }
  });

  // Keep autoScale matching state
  useEffect(() => {
    if (!groupRef.current) return;
    groupRef.current.scale.setScalar(autoScale * gestureScaleRef.current);
  }, [autoScale]);

  return (
    <group ref={groupRef} onClick={handleClick}>
      {/* Inner group offsets the model centre to origin */}
      <group position={centerOffset.toArray()}>
        <group rotation={[0, faceViewer ? Math.PI : 0, 0]}>
        {order.map((id) => {
          const g = groups[id];
          // Only groups that have been visible at least once are fetched.
          if (!g || !g.requested) return null;
          return (
            <Suspense key={`${studyKey}:${id}`} fallback={null}>
              <SystemLayer
                url={g.url}
                color={g.color}
                emissive={g.emissive}
                visible={g.visible}
                hidden={hidden}
                onLoaded={handleLoaded(id)}
              />
            </Suspense>
          );
        })}
        </group>
      </group>
    </group>
  );
}

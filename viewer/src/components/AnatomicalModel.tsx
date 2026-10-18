import { Suspense, useRef, useEffect, useState, useCallback, useMemo } from 'react';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import * as THREE from 'three';
import SystemLayer from './SystemLayer';
import { useLayerStore } from '../store/layerStore';
import { useStudyStore } from '../store/studyStore';
import { getFingerRay } from '../utils/raycastUtils';
import type { GestureState } from '../hooks/useGestureControl';
import type { Pt } from '../hooks/gestureEngine';
import { COMMIT } from '../hooks/selectionEngine';

// how far cleared parts fly, in view widths, along the sweep's direction
const FLY = 1.2;

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
  activeGesture: string;
  // selection & snapshot props
  gestureState: GestureState;
  selectedMesh: string | null;
  setSelectedMesh: (mesh: string | null) => void;
  /** a click (hand or mouse) on a structure (its name) or on empty space (null); Hologram
   *  resolves it with gestureEngine.resolveClick */
  onPick: (target: string | null) => void;
  /** the snapshot gesture fired: `capture` reads the frame, if the parent decides to (atlas only) */
  onAiSnapshot: (capture: () => string) => void;
  setHoveredMesh: (mesh: string | null) => void;
  partsRemoved: boolean;
  /** the other hand's live sweep (screen fractions): the unselected parts follow it */
  removal: { offset: Pt; live: boolean };
  /** the direction the cleared parts flew off in (screen, unit length) */
  flyDir: Pt;
  /** turn the model half a turn to face the camera (pipeline studies); false for the atlas */
  faceViewer: boolean;
}

export default function AnatomicalModel({
  modelPosition,
  modelRotationX,
  modelRotationY,
  modelScale,
  handsVisible,
  activeGesture,
  gestureState,
  selectedMesh,
  setSelectedMesh,
  onPick,
  onAiSnapshot,
  setHoveredMesh,
  partsRemoved,
  removal,
  flyDir,
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
  const snapshotSeenRef = useRef(gestureState.snapshotId);

  // scratch vectors for the removal animation (no per-frame allocation)
  const scratch = useMemo(() => ({
    right: new THREE.Vector3(), up: new THREE.Vector3(), delta: new THREE.Vector3(),
    world: new THREE.Vector3(), local: new THREE.Vector3(), centre: new THREE.Vector3(),
  }), []);
  const partsRemovedRef = useRef(partsRemoved);
  useEffect(() => { partsRemovedRef.current = partsRemoved; }, [partsRemoved]);

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

  /** The structure a list of ray hits lands on: the first visible mesh, and while a part is
   *  isolated only that part (the faded-out rest stays loaded and would otherwise catch rays). */
  const pickFrom = useCallback((hits: THREE.Intersection[]): THREE.Object3D | null => {
    const g = groupRef.current;
    if (!g) return null;
    const hit = hits.find((h) =>
      (h.object as THREE.Mesh).isMesh && visibleChain(h.object)
      && (!partsRemovedRef.current || within(h.object, selectedMeshRef.current)));
    return hit ? rootOf(hit.object, structuresRef.current, g) : null;
  }, []);

  // Mouse path: the same click rules as the hands. A click that ended an orbit drag is ignored;
  // a click on empty space is reported by HologramCanvas (onPointerMissed).
  // Mouse hover (the room redesign; additive, like the mouse click): R3F raycasts only on pointer
  // moves; the frame loop uses this whenever no hand cursor is up, so the part is named and lit.
  const mouseHoverRef = useRef<THREE.Object3D | null>(null);
  const handlePointerMove = useCallback((e: ThreeEvent<PointerEvent>) => {
    mouseHoverRef.current = pickFrom(e.intersections);
  }, [pickFrom]);
  const handlePointerOut = useCallback(() => { mouseHoverRef.current = null; }, []);

  const handleClick = useCallback((e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4) return;
    e.stopPropagation();
    const root = pickFrom(e.intersections);
    onPick(root ? root.name : null);
  }, [pickFrom, onPick]);

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

    // follow the hands while they are in view; when they leave, the model stays where it was
    const lerpPosRot = handsVisible;
    const lerpPos = handsVisible;
    const lerpScale = handsVisible;
    void activeGesture;

    g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, modelRotationY,  lerpPosRot ? 0.12 : 0);
    g.rotation.x = THREE.MathUtils.lerp(g.rotation.x, modelRotationX,  lerpPosRot ? 0.12 : 0);
    g.position.x = THREE.MathUtils.lerp(g.position.x, modelPosition.x, lerpPos ? 0.22 : 0);
    g.position.y = THREE.MathUtils.lerp(g.position.y, modelPosition.y, lerpPos ? 0.22 : 0);
    g.scale.setScalar(THREE.MathUtils.lerp(g.scale.x,  modelScale * autoScale, lerpScale ? 0.18 : 0));
    // the room's stand follows this group (HallStage, lib/stageFollow): it needs the auto-fit to
    // tell the gesture zoom apart
    g.userData.fit = autoScale;

    // ── Hover: the hand's cursor, whenever it is not grabbing (no freeze mode); else the mouse ──
    let currentHovered: THREE.Object3D | null = null;
    if (gestureState.cursor && !gestureState.grabbing) {
      const ray = getFingerRay(gestureState.cursor.x, gestureState.cursor.y, camera);
      currentHovered = pickFrom(ray.intersectObject(g, true));
    } else if (!handsVisible) {
      currentHovered = mouseHoverRef.current;
    }
    if (currentHovered !== hoveredMeshRef.current) {
      hoveredMeshRef.current = currentHovered;
      setHoveredMesh(currentHovered ? currentHovered.name : null);
    }

    // ── Dynamic Mesh Animations (Scale, Opacity, and Glowing) ───────────
    const hasSelection = selectedMeshRef.current !== null;

    if (allMeshesRef.current.length > 0 && hasSelection) {
      // Where the unselected parts go, in screen fractions: with the other hand's live sweep;
      // off along the sweep once cleared; back in from there while a new sweep restores them.
      const k = Math.min(1, Math.hypot(removal.offset.x, removal.offset.y) / COMMIT);
      let sx = 0, sy = 0, fade = 1;
      if (!partsRemoved) {
        if (removal.live) { sx = removal.offset.x; sy = removal.offset.y; fade = 1 - 0.75 * k; }
      } else {
        const back = removal.live ? k : 0;
        sx = flyDir.x * FLY * (1 - back);
        sy = flyDir.y * FLY * (1 - back);
        fade = back;
      }
      // screen fractions -> a world displacement in the camera's plane, sized at the model's depth
      const cam = camera as THREE.PerspectiveCamera;
      const depth = cam.position.distanceTo(g.getWorldPosition(scratch.centre));
      const viewH = 2 * depth * Math.tan(THREE.MathUtils.degToRad((cam.fov ?? 50) / 2));
      const viewW = viewH * (cam.aspect ?? 1);
      scratch.right.set(1, 0, 0).applyQuaternion(cam.quaternion);
      scratch.up.set(0, 1, 0).applyQuaternion(cam.quaternion);
      scratch.delta.copy(scratch.right).multiplyScalar(sx * viewW).addScaledVector(scratch.up, -sy * viewH);
      const moving = sx !== 0 || sy !== 0;
      const posLerp = removal.live ? 0.35 : 0.1;
      const inverses = new Map<string, THREE.Matrix4>(); // one inverse per parent per frame

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

        // 1. Position: the selection stays; the rest moves by the world displacement, carried into
        //    each mesh's parent frame, so the motion matches the hand on screen whatever the
        //    model's rotation or the GLB's nesting
        let target: THREE.Vector3 = mesh.userData.originalPosition;
        if (!isSelected && moving && mesh.parent) {
          const parent = mesh.parent;
          let inv = inverses.get(parent.uuid);
          if (!inv) { inv = parent.matrixWorld.clone().invert(); inverses.set(parent.uuid, inv); }
          scratch.world.copy(mesh.userData.originalPosition).applyMatrix4(parent.matrixWorld).add(scratch.delta);
          target = scratch.local.copy(scratch.world).applyMatrix4(inv);
        }
        mesh.position.lerp(target, isSelected ? 0.12 : posLerp);

        // 2. Opacity: the selection full; the rest dimmed, fading as it is swept or cleared
        const targetOpacity = isSelected ? 1.0 : 0.15 * fade;
        mat.opacity = THREE.MathUtils.lerp(mat.opacity ?? 1.0, targetOpacity, removal.live ? 0.3 : 0.12);
        mat.transparent = true;

        // 2. Calculate Target Scale (selected renders at normal scale)
        const targetScaleFactor = 1.0;
        const targetScale = mesh.userData.originalScale.clone().multiplyScalar(targetScaleFactor);
        mesh.scale.x = THREE.MathUtils.lerp(mesh.scale.x, targetScale.x, 0.12);
        mesh.scale.y = THREE.MathUtils.lerp(mesh.scale.y, targetScale.y, 0.12);
        mesh.scale.z = THREE.MathUtils.lerp(mesh.scale.z, targetScale.z, 0.12);

        // 3. Calculate Target Emissive Intensity (pulsing glow)
        // the room redesign: a soft breathing glow in the part's own colour, not blown-out neon
        let targetEmissive = 0.04;
        if (isSelected) {
          const elapsed = state.clock.getElapsedTime();
          targetEmissive = 0.45 + Math.sin(elapsed * 4) * 0.1;
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

        let targetOpacity = 1.0;

        // Smoothly animate position & opacity. All three axes: a sweep moves parts in the camera's
        // plane, which has depth in the model's frame once the view has turned
        mesh.position.lerp(mesh.userData.originalPosition, 0.12);

        mat.opacity = THREE.MathUtils.lerp(mat.opacity ?? 1.0, targetOpacity, 0.12);
        mat.transparent = true;

        // 2. Calculate Target Scale (hovered pops out slightly, others normal scale)
        let targetScaleFactor = isHovered ? 1.05 : 1.0;
        const targetScale = mesh.userData.originalScale.clone().multiplyScalar(targetScaleFactor);
        mesh.scale.x = THREE.MathUtils.lerp(mesh.scale.x, targetScale.x, 0.12);
        mesh.scale.y = THREE.MathUtils.lerp(mesh.scale.y, targetScale.y, 0.12);
        mesh.scale.z = THREE.MathUtils.lerp(mesh.scale.z, targetScale.z, 0.12);

        // 3. Calculate Target Emissive Intensity (pulsing glow)
        let targetEmissive = 0.04;
        if (isHovered) {
          const elapsed = state.clock.getElapsedTime();
          targetEmissive = 0.3 + Math.sin(elapsed * 4) * 0.06; // hover: a gentler glow than selection
        }

        mat.emissiveIntensity = THREE.MathUtils.lerp(
          mat.emissiveIntensity,
          targetEmissive,
          0.15
        );
      });
    }

    // ── AI snapshot: the gesture engine detects it (both index fingers pointing at each other,
    //    once per touch); the parent decides whether the frame is read at all ──────────────────
    if (gestureState.snapshotId !== snapshotSeenRef.current) {
      snapshotSeenRef.current = gestureState.snapshotId;
      // a JPEG: a few hundred KB for this dark scene (the server takes at most ~1.5 MB)
      onAiSnapshot(() => gl.domElement.toDataURL('image/jpeg', 0.82));
    }
  });

  // Keep autoScale matching state
  useEffect(() => {
    if (!groupRef.current) return;
    groupRef.current.scale.setScalar(autoScale * gestureScaleRef.current);
  }, [autoScale]);

  return (
    <group ref={groupRef} name="anatomy-model" onClick={handleClick} onPointerMove={handlePointerMove} onPointerOut={handlePointerOut}>
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

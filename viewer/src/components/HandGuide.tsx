import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as THREE from 'three';
import {
  ArrowCounterClockwise, ArrowsOutSimple, Camera, HandFist, HandGrabbing, HandPalm, HandPointing, HandSwipeLeft,
} from '@phosphor-icons/react';
import { HAND_EDGES } from '../data/handPoses';
import { LOOP_S, MOVES, frameFor, type GuideHand, type MoveKey } from '../data/guideMoves';

/** The "How to use" guide (gestures v3): each move shown by a 3D hand, with a stand-in body that
 *  reacts the way the real one does (data/guideMoves.ts). A small three.js scene, built and owned
 *  by one effect, alive only while the guide is open. */

const ICON: Record<MoveKey, ReactNode> = {
  rest: <HandPalm size={17} />,
  turn: <HandGrabbing size={17} />,
  move: <HandFist size={17} />,
  zoom: <ArrowsOutSimple size={17} />,
  select: <HandPointing size={17} />,
  clear: <HandSwipeLeft size={17} />,
  back: <ArrowCounterClockwise size={17} />,
  snapshot: <Camera size={17} />,
};

const REDUCED = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const HAND_SCALE = 0.25;
// the palm, filled between the wrist and the knuckles so the hand reads as a hand
const PALM = [0, 1, 5, 9, 13, 17];

/** Builds the guide scene in `host` and animates the move `playing` holds; returns a disposer. */
type Playing = { move: MoveKey; since: number };

function mountGuideScene(host: HTMLDivElement, playing: { readonly current: Playing }): () => void {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 50);
  camera.position.set(0, 0.1, 6.4);
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 1.2); key.position.set(3, 4, 5); scene.add(key);
  const fill = new THREE.DirectionalLight(0xc9f2df, 0.4); fill.position.set(-3, 1, 2); scene.add(fill);

  const disposables: { dispose: () => void }[] = [];
  const keep = <T extends { dispose: () => void }>(x: T) => { disposables.push(x); return x; };

  // the stand-in body: a capsule with two parts that can be cleared, and the part that is selected
  const restMat = keep(new THREE.MeshStandardMaterial({ color: 0xdfe7e2, roughness: 0.6, transparent: true, depthWrite: false }));
  const partMats = [0xeb6834, 0x1baf7a].map((c) => keep(new THREE.MeshStandardMaterial({ color: c, roughness: 0.5, transparent: true, depthWrite: false })));
  const heartMat = keep(new THREE.MeshStandardMaterial({ color: 0xe34948, emissive: 0xe34948, emissiveIntensity: 0, roughness: 0.4 }));
  const model = new THREE.Group();
  const rest = new THREE.Group();
  rest.add(new THREE.Mesh(keep(new THREE.CapsuleGeometry(0.42, 1.0, 8, 24)), restMat));
  const sphere = keep(new THREE.SphereGeometry(1, 24, 16));
  const part = (mat: THREE.Material, r: number, x: number, y: number, z: number) => {
    const m = new THREE.Mesh(sphere, mat); m.scale.setScalar(r); m.position.set(x, y, z); return m;
  };
  rest.add(part(partMats[0], 0.15, 0.2, -0.05, 0.36), part(partMats[1], 0.13, -0.2, 0.42, 0.32));
  model.add(rest, part(heartMat, 0.15, 0.04, 0.32, 0.4));
  // the hold ring: 24 dots around the selected part, lit as the hold fills
  const ringMat = keep(new THREE.MeshBasicMaterial({ color: 0xc9f2df }));
  const dots = Array.from({ length: 24 }, (_, i) => {
    const a = Math.PI / 2 - (i / 24) * Math.PI * 2;
    const d = part(ringMat, 0.025, 0.04 + Math.cos(a) * 0.27, 0.32 + Math.sin(a) * 0.27, 0.55);
    d.visible = false; model.add(d); return d;
  });
  scene.add(model);

  // two hands: joints are spheres, bones are cylinders
  const handMat = keep(new THREE.MeshStandardMaterial({ color: 0xd8f6e8, emissive: 0x2f9b75, emissiveIntensity: 0.5, roughness: 0.45 }));
  const palmMat = keep(new THREE.MeshStandardMaterial({ color: 0xc9f2df, emissive: 0x2f9b75, emissiveIntensity: 0.4, roughness: 0.6, side: THREE.DoubleSide, transparent: true, opacity: 0.85 }));
  const bone = keep(new THREE.CylinderGeometry(0.24, 0.24, 1, 12));
  const hands = [0, 1].map(() => {
    const group = new THREE.Group();
    const joints = Array.from({ length: 21 }, (_, i) => { const j = new THREE.Mesh(sphere, handMat); j.scale.setScalar(i % 4 === 0 && i > 0 ? 0.26 : 0.3); group.add(j); return j; });
    const bones = HAND_EDGES.map(() => { const b = new THREE.Mesh(bone, handMat); group.add(b); return b; });
    const palmGeo = keep(new THREE.BufferGeometry());
    palmGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(PALM.length * 3), 3));
    palmGeo.setIndex([0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 5]);
    group.add(new THREE.Mesh(palmGeo, palmMat));
    // the arming ring around the palm (the other hand held still before a sweep), in hand units
    const ring = Array.from({ length: 16 }, (_, i) => {
      const a = Math.PI / 2 - (i / 16) * Math.PI * 2;
      const dot = new THREE.Mesh(sphere, ringMat);
      dot.scale.setScalar(0.16);
      dot.position.set(0.27 + Math.cos(a) * 3.3, 1.85 + Math.sin(a) * 3.3, 0.4);
      dot.visible = false; group.add(dot); return dot;
    });
    group.visible = false; scene.add(group);
    return { group, joints, bones, palmGeo, ring };
  });
  // the snapshot's flash: a white veil in front of everything
  const flashMat = keep(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, depthTest: false, depthWrite: false }));
  const flashPlane = new THREE.Mesh(keep(new THREE.PlaneGeometry(30, 30)), flashMat);
  flashPlane.position.z = 4; flashPlane.renderOrder = 10; scene.add(flashPlane);
  const UP = new THREE.Vector3(0, 1, 0), a = new THREE.Vector3(), b = new THREE.Vector3(), d = new THREE.Vector3();
  const pose = (h: (typeof hands)[number], hand: GuideHand | undefined) => {
    h.group.visible = !!hand;
    if (!hand) return;
    h.group.position.set(...hand.at);
    h.group.rotation.set(0, 0, hand.mirror ? -(hand.tilt ?? 0) : (hand.tilt ?? 0));
    h.group.scale.set(hand.mirror ? -HAND_SCALE : HAND_SCALE, HAND_SCALE, HAND_SCALE);
    hand.pose.forEach((p, i) => h.joints[i].position.set(p[0], p[1], p[2]));
    const palm = h.palmGeo.getAttribute('position') as THREE.BufferAttribute;
    PALM.forEach((j, i) => palm.setXYZ(i, ...hand.pose[j]));
    palm.needsUpdate = true;
    h.palmGeo.computeVertexNormals();
    h.palmGeo.computeBoundingSphere();
    HAND_EDGES.forEach(([i, j], n) => {
      a.set(...hand.pose[i]); b.set(...hand.pose[j]); d.subVectors(b, a);
      const length = d.length();
      h.bones[n].position.copy(a).addScaledVector(d, 0.5);
      h.bones[n].quaternion.setFromUnitVectors(UP, d.normalize());
      h.bones[n].scale.set(1, length, 1);
    });
  };

  const resize = () => {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  let raf = 0;
  const tick = (now: number) => {
    const { move, since } = playing.current;
    const u = REDUCED ? 0.8 : ((Math.max(0, now - since) / 1000) % LOOP_S) / LOOP_S;
    const f = frameFor(move, u);
    hands.forEach((h, i) => pose(h, f.hands[i]));
    hands.forEach((h, i) => h.ring.forEach((dot, n) => { dot.visible = i === 1 && f.palmRing > 0 && n < Math.round(f.palmRing * 16); }));
    flashMat.opacity = f.flash * 0.55;
    flashPlane.visible = f.flash > 0;
    model.rotation.y = f.model.rotY;
    model.position.set(f.model.x, f.model.y, 0);
    model.scale.setScalar(f.model.scale);
    rest.position.set(f.rest.dx, f.rest.dy, 0);
    restMat.opacity = f.rest.opacity * 0.55;
    partMats.forEach((m) => { m.opacity = f.rest.opacity; });
    heartMat.emissiveIntensity = f.selected ? 0.7 + 0.25 * Math.sin(now / 160) : 0;
    dots.forEach((dot, i) => { dot.visible = f.ring > 0 && i < Math.round(f.ring * 24); });
    renderer.render(scene, camera);
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  return () => {
    cancelAnimationFrame(raf);
    observer.disconnect();
    disposables.forEach((x) => x.dispose());
    renderer.dispose();
    renderer.domElement.remove();
  };
}

function GuideCanvas({ move }: { move: MoveKey }) {
  const host = useRef<HTMLDivElement>(null);
  const playing = useRef<Playing>({ move, since: 0 });

  useEffect(() => {
    // each move starts from the beginning of its loop
    playing.current = { move, since: performance.now() };
  }, [move]);

  useEffect(() => {
    if (!host.current) return;
    return mountGuideScene(host.current, playing);
  }, []);

  return <div className="hand-guide__canvas" ref={host} aria-hidden="true" />;
}

export default function HandGuide({ atlas, onClose }: { atlas: boolean; onClose: () => void }) {
  const moves = MOVES.filter((m) => atlas || !m.atlasOnly);
  const [move, setMove] = useState<MoveKey>('rest');
  const [auto, setAuto] = useState(!REDUCED);

  // walk through the moves until one is chosen
  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => {
      setMove((m) => {
        const list = MOVES.filter((x) => atlas || !x.atlasOnly);
        return list[(list.findIndex((x) => x.key === m) + 1) % list.length].key;
      });
    }, LOOP_S * 1000);
    return () => window.clearInterval(id);
  }, [auto, atlas]);

  const current = moves.find((m) => m.key === move) ?? moves[0];
  return (
    <div className="hand-guide hud-interactive" data-testid="gesture-guide" role="dialog" aria-label="How to use your hands">
      <div className="hand-guide__list" role="tablist" aria-label="Moves">
        {moves.map((m) => (
          <button key={m.key} type="button" role="tab" aria-selected={m.key === move} className="hand-guide__move"
            onClick={() => { setAuto(false); setMove(m.key); }}>
            <span className="hand-guide__icon">{ICON[m.key]}</span>
            <span>{m.title}</span>
          </button>
        ))}
      </div>
      <div className="hand-guide__stage">
        <GuideCanvas move={move} />
        <p className="hand-guide__title">{current.title}</p>
        <p className="hand-guide__how">{current.how}</p>
      </div>
      <div className="hand-guide__foot">
        <p>
          Either hand steers. Both hands pinching zoom. An open palm, either hand, held still then swept sideways, clears the rest.
          {' '}Mouse: drag to turn, scroll to zoom, right-drag to move; click a part, click it again to isolate.
        </p>
        <button type="button" className="ai-run" onClick={onClose}>Got it</button>
      </div>
    </div>
  );
}

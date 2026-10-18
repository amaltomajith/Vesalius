import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { WarningOctagon } from '@phosphor-icons/react';
import HologramCanvas from '../components/HologramCanvas';
import HUDOverlay from '../components/HUDOverlay';
import HandTracker, { type CameraStatus, type NormalizedLandmark } from '../components/HandTracker';
import PartInfoPanel from '../components/PartInfoPanel';
import { useGestureControl } from '../hooks/useGestureControl';
import { resolveClick, type Pt } from '../hooks/gestureEngine';
import { initialDwell, initialSweep, selectionTick } from '../hooks/selectionEngine';
import { useStudyStore } from '../store/studyStore';
import { aiStatus } from '../services/ai';
import { decideSnapshot } from '../lib/snapshot';

const HANDS_KEY = 'vesalius.hands';

function readHandsPref(): boolean {
  try { return localStorage.getItem(HANDS_KEY) !== 'off'; } catch { return true; }
}

/**
 * The hologram: one engine for a pipeline study (/app/hologram/:studyId) and for the full-body
 * atlas (/app/body). Hands (gestures v3.1: point and hold to select, an armed open palm of the
 * other hand sweeps the rest away; gestureEngine.ts, selectionEngine.ts) and mouse (click, click again to isolate, empty
 * space to step back) share one selection. The camera runs only while open.
 */
export default function Hologram({ scene }: { scene?: 'atlas' }) {
  const params = useParams();
  const sceneId = scene === 'atlas' ? 'atlas' : (params.studyId ?? '');
  const openById = useStudyStore((s) => s.openById);
  const current = useStudyStore((s) => s.current);
  const loading = useStudyStore((s) => s.loading);
  const error = useStudyStore((s) => s.error);
  const atlas = current?.manifest.kind === 'atlas';

  const { gestureState, updateHands } = useGestureControl();
  const [selectedMesh, setSelectedMesh] = useState<string | null>(null);
  const [hoveredMesh, setHoveredMesh] = useState<string | null>(null);
  const [partsRemoved, setPartsRemoved] = useState(false);
  // the hands' selection: hold progress, the live sweep of the other hand, where cleared parts went
  const [dwellProgress, setDwellProgress] = useState(0);
  const [removal, setRemoval] = useState<{ offset: Pt; live: boolean }>({ offset: { x: 0, y: 0 }, live: false });
  const [flyDir, setFlyDir] = useState<Pt>({ x: 1, y: 0 });
  const [sweepArmed, setSweepArmed] = useState(false);
  const [sweepArming, setSweepArming] = useState(0);
  const [ignoredHands, setIgnoredHands] = useState(0);
  // one line of feedback for the snapshot gesture (it has no other visible effect)
  const [flash, setFlash] = useState<{ text: string; id: number } | null>(null);
  const dwellRef = useRef(initialDwell());
  const sweepRef = useRef(initialSweep());
  const hoveredRef = useRef<string | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [handsOn, setHandsOn] = useState(readHandsPref);
  const [camera, setCamera] = useState<CameraStatus | 'off'>('off');
  const [fullscreen, setFullscreen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // Request sequence guard (VIEWER-CONSTRAINTS: keep it). Anything asynchronous keyed on the
  // selection checks its id is still current before setting state.
  const activeRequestRef = useRef<number>(0);

  useEffect(() => { void openById(sceneId); }, [openById, sceneId]);

  useEffect(() => {
    activeRequestRef.current++; // invalidate anything in flight for the previous selection
    setSnapshot(null);
    if (!selectedMesh) setPartsRemoved(false);
  }, [selectedMesh]);

  // one click model for hands and mouse: a part, or empty space (null), one level at a time
  const selectionRef = useRef({ selected: selectedMesh, isolated: partsRemoved });
  useEffect(() => { selectionRef.current = { selected: selectedMesh, isolated: partsRemoved }; }, [selectedMesh, partsRemoved]);
  useEffect(() => { hoveredRef.current = hoveredMesh; }, [hoveredMesh]);

  // mouse and panel buttons: a click; isolating this way sends the rest off to the right
  const handlePick = useCallback((target: string | null) => {
    const next = resolveClick(selectionRef.current, target);
    if (next.isolated && !selectionRef.current.isolated) setFlyDir({ x: 1, y: 0 });
    setSelectedMesh(next.selected);
    setPartsRemoved(next.isolated);
  }, []);

  const handleRestore = useCallback(() => setPartsRemoved(false), []);
  const toggleIsolate = useCallback(() => {
    if (!selectionRef.current.isolated) setFlyDir({ x: 1, y: 0 });
    setPartsRemoved((v) => !v);
  }, []);

  // every camera frame: steer (inside updateHands), then the hold and the other hand's sweep
  const handleHandsUpdate = useCallback((hands: NormalizedLandmark[][] | null, handedness?: string[]) => {
    const g = updateHands(hands, handedness);
    const now = performance.now();
    const sel = selectionRef.current;
    const t = selectionTick({ dwell: dwellRef.current, sweep: sweepRef.current, sel }, g, hoveredRef.current, now);
    dwellRef.current = t.state.dwell;
    sweepRef.current = t.state.sweep;
    const next = t.state.sel;
    const sw = t.sweep;
    setDwellProgress((p) => (Math.abs(p - t.progress) > 0.01 || (t.progress === 0 && p !== 0) ? t.progress : p));
    setRemoval((r) => (r.live === sw.live && Math.abs(r.offset.x - sw.offset.x) < 0.002 && Math.abs(r.offset.y - sw.offset.y) < 0.002
      ? r : { offset: sw.offset, live: sw.live }));
    setSweepArmed(sw.armed);
    setSweepArming((a) => (Math.abs(a - sw.arming) > 0.04 || (sw.arming === 0 && a !== 0) ? sw.arming : a));
    if (sw.event?.kind === 'isolate') setFlyDir(sw.event.dir);

    if (next.selected !== sel.selected || next.isolated !== sel.isolated) {
      selectionRef.current = next; // the effect would catch up a render later; keep frames in step
      setSelectedMesh(next.selected);
      setPartsRemoved(next.isolated);
    }
  }, [updateHands]);

  // The two-finger snapshot gesture: for the atlas (not a patient) so the optional AI can see the
  // view; for a patient reconstruction the frame is never even read (CLAUDE.md hard rule 8).
  const atlasRef = useRef(atlas);
  atlasRef.current = atlas;
  const say = useCallback((text: string) => setFlash((f) => ({ text, id: (f?.id ?? 0) + 1 })), []);
  const handleAiSnapshot = useCallback((capture: () => string) => {
    const d = decideSnapshot(atlasRef.current, selectionRef.current.selected);
    if (!d.capture) { say(d.message); return; }
    setSnapshot(capture());
    void aiStatus().then((st) => say(d.message(st.vision)));
  }, [say]);
  useEffect(() => {
    if (!flash) return;
    const id = window.setTimeout(() => setFlash(null), 3200);
    return () => window.clearTimeout(id);
  }, [flash]);

  const toggleHands = useCallback(() => {
    setHandsOn((on) => {
      const next = !on;
      try { localStorage.setItem(HANDS_KEY, next ? 'on' : 'off'); } catch { /* per-viewer convenience only */ }
      if (!next) setCamera('off');
      return next;
    });
  }, []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void rootRef.current?.requestFullscreen?.().catch(() => { /* not allowed here: stays windowed */ });
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(!!document.fullscreenElement);
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'f' || e.key === 'F') && !(e.target instanceof HTMLInputElement)) toggleFullscreen();
    };
    document.addEventListener('fullscreenchange', onChange);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      window.removeEventListener('keydown', onKey);
      if (document.fullscreenElement) void document.exitFullscreen();
    };
  }, [toggleFullscreen]);

  const backTo = atlas || sceneId === 'atlas' ? '/' : '/studies';
  const refused = error && error.studyId === sceneId;

  return (
    <main className="room" ref={rootRef} data-testid="hologram">
      {!refused && (
        <HologramCanvas
          gestureState={gestureState}
          selectedMesh={selectedMesh}
          setSelectedMesh={setSelectedMesh}
          onPick={handlePick}
          removal={removal}
          flyDir={flyDir}
          onAiSnapshot={handleAiSnapshot}
          setHoveredMesh={setHoveredMesh}
          partsRemoved={partsRemoved}
          faceViewer={!(atlas || sceneId === 'atlas')}
        />
      )}

      {handsOn && <HandTracker onHandsUpdate={handleHandsUpdate} onStatus={setCamera} onIgnored={setIgnoredHands} />}

      <HUDOverlay
        gestureState={gestureState}
        hoveredMesh={hoveredMesh}
        selectedMesh={selectedMesh}
        partsRemoved={partsRemoved}
        dwellProgress={dwellProgress}
        sweeping={removal.live}
        sweepArmed={sweepArmed}
        sweepArming={sweepArming}
        ignoredHands={handsOn ? ignoredHands : 0}
        flash={flash?.text ?? null}
        onRestore={handleRestore}
        camera={camera}
        handsOn={handsOn}
        onToggleHands={toggleHands}
        fullscreen={fullscreen}
        onToggleFullscreen={toggleFullscreen}
        backTo={backTo}
      />

      <PartInfoPanel
        selectedMesh={selectedMesh}
        onClose={() => setSelectedMesh(null)}
        isolated={partsRemoved}
        onToggleIsolate={toggleIsolate}
        snapshot={atlas ? snapshot : null}
      />

      {loading === sceneId && !current && (
        <div className="room-state" data-testid="scene-loading">
          <p>{sceneId === 'atlas' ? 'Loading the full body' : 'Loading the study'}</p>
        </div>
      )}

      {refused && (
        <div className="room-state room-state--refused hud-interactive" role="alert" data-testid="scene-refused">
          <WarningOctagon size={30} />
          <h2>{error.refused ? 'Refused' : 'Could not open this study'}</h2>
          <p>{error.message}</p>
          {error.refused && (
            <p className="room-state__why">
              The viewer checks every study against the mesh contract and will not show a head
              study whose face was not removed. Nothing from it was drawn.
            </p>
          )}
          <Link className="btn btn-mint btn-sm" to="/studies">Back to studies</Link>
        </div>
      )}
    </main>
  );
}

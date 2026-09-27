import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { WarningOctagon } from '@phosphor-icons/react';
import HologramCanvas from '../components/HologramCanvas';
import HUDOverlay from '../components/HUDOverlay';
import HandTracker, { type CameraStatus, type NormalizedLandmark } from '../components/HandTracker';
import PartInfoPanel from '../components/PartInfoPanel';
import { useGestureControl } from '../hooks/useGestureControl';
import { useStudyStore } from '../store/studyStore';

const HANDS_KEY = 'phantom.hands';

function readHandsPref(): boolean {
  try { return localStorage.getItem(HANDS_KEY) !== 'off'; } catch { return true; }
}

/**
 * The hologram: one engine for a pipeline study (/app/hologram/:studyId) and for the full-body
 * atlas (/app/body). Holoscan's behaviour is unchanged (gesture engine, freeze, hover, select,
 * isolate, restore); the camera runs only while this screen is open.
 */
export default function Hologram({ scene }: { scene?: 'atlas' }) {
  const params = useParams();
  const sceneId = scene === 'atlas' ? 'atlas' : (params.studyId ?? '');
  const openById = useStudyStore((s) => s.openById);
  const current = useStudyStore((s) => s.current);
  const loading = useStudyStore((s) => s.loading);
  const error = useStudyStore((s) => s.error);
  const atlas = current?.manifest.kind === 'atlas';

  const { gestureState, updateHands, clearSwipeGestures } = useGestureControl();
  const [selectedMesh, setSelectedMesh] = useState<string | null>(null);
  const [hoveredMesh, setHoveredMesh] = useState<string | null>(null);
  const [partsRemoved, setPartsRemoved] = useState(false);
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

  // Reset selected mesh and parts removed when exiting freeze mode
  useEffect(() => {
    if (!gestureState.isFrozen) {
      setSelectedMesh(null);
      setPartsRemoved(false);
    }
  }, [gestureState.isFrozen]);

  // Right-hand swipe isolates the selected part
  useEffect(() => {
    if (selectedMesh && gestureState.rightSwipeGesture) setPartsRemoved(true);
  }, [selectedMesh, gestureState.rightSwipeGesture]);

  const handleRestore = useCallback(() => {
    setPartsRemoved(false);
    clearSwipeGestures();
  }, [clearSwipeGestures]);

  const handleHandsUpdate = useCallback(
    (hands: NormalizedLandmark[][] | null, handedness?: string[]) => updateHands(hands, handedness),
    [updateHands],
  );

  // The two-finger snapshot gesture: kept for the atlas (not a patient) so the optional AI can
  // see the view; for a patient reconstruction the frame is discarded (CLAUDE.md hard rule 8).
  const atlasRef = useRef(atlas);
  atlasRef.current = atlas;
  const handleAiSnapshot = useCallback((frame: string) => {
    if (atlasRef.current) setSnapshot(frame);
  }, []);

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
          onAiSnapshot={handleAiSnapshot}
          setHoveredMesh={setHoveredMesh}
          partsRemoved={partsRemoved}
          faceViewer={!(atlas || sceneId === 'atlas')}
        />
      )}

      {handsOn && <HandTracker onHandsUpdate={handleHandsUpdate} onStatus={setCamera} />}

      <HUDOverlay
        gestureState={gestureState}
        hoveredMesh={hoveredMesh}
        partsRemoved={partsRemoved}
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
        leftFistActive={gestureState.leftFistActive}
        snapshot={atlas ? snapshot : null}
      />

      {loading === sceneId && !current && (
        <div className="room-state" data-testid="scene-loading">
          <div className="glb-loading-ring" />
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

import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import {
  ArrowCounterClockwise, ArrowLeft, ArrowsOutSimple, Camera, CameraSlash, CornersIn, CornersOut,
  Hand, HandFist, HandGrabbing, HandPeace, HandPointing, HandSwipeRight, Question, Snowflake,
  TreeStructure, X,
} from '@phosphor-icons/react';
import type { GestureState } from '../hooks/useGestureControl';
import type { CameraStatus } from './HandTracker';
import LayerPanel from './LayerPanel';
import AuditDrawer from './AuditDrawer';
import { getAnatomyInfo } from '../data/anatomyDatabase';
import { useStudyStore } from '../store/studyStore';

interface HUDOverlayProps {
  gestureState: GestureState;
  hoveredMesh?: string | null;
  partsRemoved?: boolean;
  onRestore?: () => void;
  camera: CameraStatus | 'off';
  handsOn: boolean;
  onToggleHands: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  backTo: string;
}

const STATUS: Record<GestureState['activeGesture'], { label: string; icon: ReactNode }> = {
  ROTATE: { label: 'Rotating', icon: <HandGrabbing size={13} /> },
  PAN: { label: 'Panning', icon: <Hand size={13} /> },
  ZOOM: { label: 'Zooming', icon: <ArrowsOutSimple size={13} /> },
  FREEZE: { label: 'Frozen', icon: <Snowflake size={13} /> },
  NONE: { label: 'Ready', icon: <Hand size={13} /> },
};

const GESTURES: { icon: ReactNode; action: string; how: string; atlasOnly?: boolean }[] = [
  { icon: <HandGrabbing size={18} />, action: 'Rotate', how: 'Right hand: pinch and move' },
  { icon: <Hand size={18} />, action: 'Pan', how: 'Right hand: open palm and move' },
  { icon: <ArrowsOutSimple size={18} />, action: 'Zoom', how: 'Both hands: pinch, then spread or squeeze' },
  { icon: <HandFist size={18} />, action: 'Freeze / unfreeze', how: 'Right hand: hold a fist for about a second' },
  { icon: <HandPointing size={18} />, action: 'Hover a part', how: 'Left index finger, while frozen' },
  { icon: <HandPeace size={18} />, action: 'Select it', how: 'Left hand: victory sign, while frozen' },
  { icon: <HandFist size={18} />, action: 'Dismiss the panel', how: 'Left hand: fist' },
  { icon: <HandSwipeRight size={18} />, action: 'Isolate the selection', how: 'Right hand: swipe, after selecting' },
  { icon: <Camera size={18} />, action: 'Snapshot for the AI', how: 'Both index fingers point at each other', atlasOnly: true },
];

const MOUSE = 'Mouse: drag to rotate, scroll to zoom, right-drag to pan, click a part to select it.';

export default function HUDOverlay({
  gestureState, hoveredMesh, partsRemoved, onRestore, camera, handsOn, onToggleHands,
  fullscreen, onToggleFullscreen, backTo,
}: HUDOverlayProps) {
  const [fps, setFps] = useState(0);
  const [showGuide, setShowGuide] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const current = useStudyStore((s) => s.current);
  const m = current?.manifest;
  const atlas = m?.kind === 'atlas';

  // measured frame rate
  useEffect(() => {
    let frames = 0;
    let raf = 0;
    const tick = () => { frames++; raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
    const t = setInterval(() => { setFps(frames); frames = 0; }, 1000);
    return () => { cancelAnimationFrame(raf); clearInterval(t); };
  }, []);

  const { activeGesture, handsVisible, isFrozen, fistHoldProgress } = gestureState;
  let status = handsVisible || isFrozen ? STATUS[isFrozen ? 'FREEZE' : activeGesture] : { label: 'Standby', icon: <Hand size={13} /> };
  let statusTone = isFrozen ? 'frozen' : handsVisible ? 'live' : 'idle';
  const pct = Math.round(fistHoldProgress * 100);
  if (fistHoldProgress > 0) {
    status = { label: `${isFrozen ? 'Releasing' : 'Freezing'} ${pct}%`, icon: <HandFist size={13} /> };
    statusTone = 'frozen';
  }

  const structureCount = m ? m.groups.reduce((n, g) => n + g.structures.filter((s) => !s.landmark).length, 0) : 0;
  const spacing = m?.study.voxel_spacing_mm?.map((v) => +v.toFixed(2)).join('·');
  const title = atlas ? 'Full body' : m ? `${m.study.modality ?? 'Scan'} · ${m.study.body_part ?? 'unknown region'}` : 'Loading…';

  const cameraLabel = !handsOn ? 'Hands off' : camera === 'on' ? 'Hands on' : camera === 'starting' ? 'Starting camera' : camera === 'blocked' ? 'Camera blocked' : 'No camera';

  return (
    <div className="hud-container">
      <header className="hud-top-bar">
        <div className="hud-logo">
          <Link to={backTo} className="hud-back hud-interactive" aria-label="Back to Phantom">
            <ArrowLeft size={14} weight="bold" />
          </Link>
          <span className="logo-dot" />
          <span className="logo-text">PHANTOM</span>
          <span className="hud-scene-title" data-testid="scene-title">{title}</span>
        </div>

        <div className={`hud-status-pill hud-status-pill--${statusTone}`}>
          {fistHoldProgress > 0 && <div className="hud-status-pill__progress" style={{ width: `${pct}%` }} />}
          {status.icon}&nbsp;{status.label}
        </div>

        <div className="hud-top-right">
          <button
            className={`hud-chip hud-interactive ${handsOn && camera === 'on' ? 'hud-chip--on' : ''}`}
            onClick={onToggleHands}
            title={handsOn ? 'Turn hand tracking off (mouse only)' : 'Turn hand tracking on'}
            data-testid="hands-toggle"
            aria-label={cameraLabel}
          >
            {handsOn && camera === 'on' ? <Camera size={13} /> : <CameraSlash size={13} />}
            <span className="hud-chip__label">{cameraLabel}</span>
          </button>
          {!atlas && current?.summary.source === 'pipeline' && (
            <button className="hud-chip hud-interactive" onClick={() => setShowAudit((v) => !v)} data-testid="audit-toggle" aria-label="Audit trail">
              <TreeStructure size={13} /> <span className="hud-chip__label">Audit trail</span>
            </button>
          )}
          <button className="hud-chip hud-interactive" onClick={onToggleFullscreen} data-testid="fullscreen-toggle" aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
            title={fullscreen ? 'Exit full screen (F)' : 'Full screen (F)'}>
            {fullscreen ? <CornersIn size={13} /> : <CornersOut size={13} />}
            <span className="hud-chip__label">{fullscreen ? 'Exit' : 'Full screen'}</span>
          </button>
        </div>
      </header>

      <div className="hud-layer-slot">
        <LayerPanel />
      </div>

      <div className="hud-sides">
        <aside className="hud-panel hud-interactive" data-testid="study-facts">
          <div className="panel-label">{atlas ? 'Atlas' : 'Study'}</div>
          {m && atlas && (
            <>
              <Row k="Systems" v={m.groups.length} />
              <Row k="Structures" v={structureCount.toLocaleString()} />
              <Row k="Detail" v="Full" />
              <div className="panel-meta">Z-Anatomy · CC-BY-SA</div>
            </>
          )}
          {m && !atlas && (
            <>
              <Row k="Modality" v={m.study.modality ?? '·'} />
              <Row k="Body part" v={m.study.body_part ?? '·'} />
              <Row k="Slices" v={m.study.slice_count ?? '·'} />
              <Row k="Voxel" v={<>{spacing ?? '·'} <em>mm</em></>} />
              <Row k="Structures" v={structureCount || 'Whole surface'} />
              <Row k="Face" v={m.deface.applied ? 'Removed' : m.deface.required ? '·' : 'Not a head'} tone={m.deface.applied ? 'ok' : undefined} />
              {m.study.shifted_study_date && <Row k="Date" v={<>{m.study.shifted_study_date} <em>shifted</em></>} />}
            </>
          )}
          {!m && <div className="panel-meta">Loading</div>}
        </aside>

        <aside className="hud-panel hud-interactive">
          <div className="panel-label">System</div>
          <Row k="Render" v={<>{fps} <em>fps</em></>} />
          <Row k={atlas ? 'Source' : 'Segmenter'} v={atlas ? 'Holoscan atlas' : m ? `${m.segmentation.model === 'totalsegmentator' ? 'TotalSeg' : m.segmentation.model} ${m.segmentation.model_version}` : '·'} />
          <Row k="Tracking" v={handsVisible ? 'Active' : handsOn ? 'Standby' : 'Off'} tone={handsVisible ? 'ok' : undefined} />
          <div className="panel-divider" />
          <div className="panel-meta">mesh_contract {m?.contract_version ?? '·'}</div>
        </aside>
      </div>

      {isFrozen && hoveredMesh && (() => {
        const info = getAnatomyInfo(hoveredMesh);
        return (
          <div className="hud-selection-brief hud-interactive">
            <div className="hud-selection-brief__header">
              <span className="hud-selection-brief__scanline" />
              <span className="hud-selection-brief__tag">Hover target</span>
              <span className="hud-selection-brief__sys">{info.system}</span>
            </div>
            <h2 className="hud-selection-brief__title">{info.displayName}</h2>
            <div className="hud-selection-brief__divider" />
            <div className="hud-selection-brief__action">
              <span className="hud-selection-brief__icon-pulse"><HandPeace size={16} /></span>
              <span className="hud-selection-brief__instruction">Victory sign to select</span>
            </div>
          </div>
        );
      })()}

      {showAudit && current && <AuditDrawer studyId={current.summary.id} onClose={() => setShowAudit(false)} />}

      <footer className="hud-bottom">
        {m && current && (
          <div className="hud-provenance hud-interactive" data-testid="provenance">
            {atlas
              ? <>Atlas: Z-Anatomy / BodyParts3D, CC-BY-SA · parts under other licences removed</>
              : <>{current.summary.source === 'demo' ? 'bundled demo · ' : ''}pipeline {m.pipeline_version} · series {m.series_id}</>}
          </div>
        )}

        {partsRemoved && onRestore && (
          <button className="revert-btn hud-interactive" onClick={onRestore} title="Restore anatomy">
            <ArrowCounterClockwise size={14} /> Restore anatomy
          </button>
        )}

        {showGuide && (
          <div className="gesture-guide-popover hud-interactive" data-testid="gesture-guide">
            <div className="gesture-guide-popover__title">Hands</div>
            <div className="gesture-guide-grid">
              {GESTURES.filter((g) => atlas || !g.atlasOnly).map((g) => (
                <div key={g.action} className="gesture-guide-item">
                  <span className="gesture-guide-item__glyph">{g.icon}</span>
                  <div className="gesture-guide-item__body">
                    <span className="gesture-guide-item__action">{g.action}</span>
                    <span className="gesture-guide-item__desc">{g.how}</span>
                  </div>
                </div>
              ))}
            </div>
            <p className="gesture-guide-mouse">{MOUSE}</p>
          </div>
        )}

        <button id="how-to-use-btn" className="how-to-use-btn hud-interactive" onClick={() => setShowGuide((v) => !v)}>
          {showGuide ? <X size={13} /> : <Question size={13} />}
          {showGuide ? 'Close' : 'How to use'}
        </button>
      </footer>
    </div>
  );
}

function Row({ k, v, tone }: { k: string; v: ReactNode; tone?: 'ok' }) {
  return (
    <div className="panel-row">
      <span className="panel-row-key">{k}</span>
      <span className={`panel-row-val ${tone === 'ok' ? 'panel-row-val--ok' : ''}`}>{v}</span>
    </div>
  );
}

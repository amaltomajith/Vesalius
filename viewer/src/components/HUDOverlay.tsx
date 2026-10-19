import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import {
  ArrowCounterClockwise, ArrowLeft, ArrowsClockwise, ArrowsOutCardinal, ArrowsOutSimple, Camera, CameraSlash,
  CornersIn, CornersOut, Hand, HandPalm, HandPointing, HandSwipeLeft, HandTap, Question,
  ShieldCheck, Stack, TreeStructure, X,
} from '@phosphor-icons/react';
import type { GestureState } from '../hooks/useGestureControl';
import type { CameraStatus } from './HandTracker';
import LayerPanel from './LayerPanel';
import HandGuide from './HandGuide';
import AuditDrawer from './AuditDrawer';
import { getAnatomyInfo } from '../data/anatomyDatabase';
import { useStudyStore } from '../store/studyStore';

interface HUDOverlayProps {
  gestureState: GestureState;
  hoveredMesh?: string | null;
  selectedMesh?: string | null;
  partsRemoved?: boolean;
  /** the hold-to-select progress, 0..1 */
  dwellProgress?: number;
  /** the other hand is sweeping the unselected parts */
  sweeping?: boolean;
  /** the sweeping palm is armed: moving it now sweeps */
  sweepArmed?: boolean;
  /** 0..1 while the open palm holds still towards arming */
  sweepArming?: number;
  /** other people's hands in view, being ignored (operatorLock) */
  ignoredHands?: number;
  /** a one-line message (the snapshot gesture's feedback) */
  flash?: string | null;
  onRestore?: () => void;
  camera: CameraStatus | 'off';
  handsOn: boolean;
  onToggleHands: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  backTo: string;
}

const STATUS: Record<GestureState['activeGesture'], { label: string; icon: ReactNode }> = {
  ROTATE: { label: 'Turning', icon: <ArrowsClockwise size={13} /> },
  PAN: { label: 'Moving', icon: <ArrowsOutCardinal size={13} /> },
  ZOOM: { label: 'Zooming', icon: <ArrowsOutSimple size={13} /> },
  POINT: { label: 'Pointing', icon: <HandPointing size={13} /> },
  PINCH: { label: 'Pinching', icon: <HandTap size={13} /> },
  REST: { label: 'Resting', icon: <HandPalm size={13} /> },
  NONE: { label: 'Ready', icon: <Hand size={13} /> },
};

const GUIDE_SEEN = 'vesalius.handGuideSeen';
const guideSeen = () => { try { return localStorage.getItem(GUIDE_SEEN) === '1'; } catch { return true; } };

export default function HUDOverlay({
  gestureState, hoveredMesh, selectedMesh, partsRemoved, dwellProgress = 0, sweeping = false, sweepArmed = false, sweepArming = 0, ignoredHands = 0, flash = null, onRestore, camera, handsOn, onToggleHands,
  fullscreen, onToggleFullscreen, backTo,
}: HUDOverlayProps) {
  // the guide opens by itself the first time hands come on; after that only on request
  const [guide, setGuide] = useState<'auto' | 'open' | 'closed'>('auto');
  const [seen] = useState(guideSeen);
  const [showAudit, setShowAudit] = useState(false);
  const current = useStudyStore((s) => s.current);
  const m = current?.manifest;
  const atlas = m?.kind === 'atlas';

  // the mouse's hover label follows the pointer without re-rendering: a transform on the element
  const hoverChip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (hoverChip.current) hoverChip.current.style.transform = `translate3d(${e.clientX}px, ${e.clientY}px, 0)`;
    };
    window.addEventListener('pointermove', move);
    return () => window.removeEventListener('pointermove', move);
  }, []);

  const { activeGesture, handsVisible, cursor, pose, sweep } = gestureState;
  const status = sweeping ? { label: 'Sweeping', icon: <HandSwipeLeft size={13} /> }
    : sweepArmed ? { label: 'Sweep ready', icon: <HandPalm size={13} /> }
    : handsVisible ? STATUS[activeGesture] : { label: 'Standby', icon: <Hand size={13} /> };
  const statusTone = handsVisible ? 'live' : 'idle';
  const hoverName = hoveredMesh ? getAnatomyInfo(hoveredMesh).displayName : null;

  // one line that says what to do next, for hands or mouse
  const hands = handsOn && camera === 'on';
  const act = hands ? 'pinch' : 'click';
  const hint = hands && !handsVisible
    ? 'Raise a hand to the camera. Open palm rests, point to aim.'
    : !selectedMesh
      ? (hands ? 'Point at a part and hold still to select it.' : 'Click a part to select it.')
      : partsRemoved
        ? (hands ? 'Open either palm, hold it still until it lights up, then sweep sideways to bring everything back.' : 'Click empty space to show everything again.')
        : (hands ? 'Open either palm, hold it still until it lights up, then sweep sideways to clear the rest away.' : `Click the same part again to isolate it, or ${act} empty space to go back.`);
  const guideOpen = guide === 'open' || (guide === 'auto' && hands && !seen);
  const closeGuide = () => {
    setGuide('closed');
    try { localStorage.setItem(GUIDE_SEEN, '1'); } catch { /* a per-viewer convenience only */ }
  };

  const structureCount = m ? m.groups.reduce((n, g) => n + g.structures.filter((s) => !s.landmark).length, 0) : 0;
  const spacing = m?.study.voxel_spacing_mm?.map((v) => +v.toFixed(2)).join(' x ');
  const region = m?.study.body_part ? m.study.body_part.charAt(0) + m.study.body_part.slice(1).toLowerCase() : 'Scan';
  const title = atlas ? 'Full body' : m ? `${m.study.modality ?? ''} ${region}`.trim() : 'Loading';
  const summary = !m ? 'Loading'
    : atlas ? `${structureCount.toLocaleString()} structures in ${m.groups.length} systems`
    : [m.study.modality, m.study.slice_count ? `${m.study.slice_count} slices` : null,
      structureCount ? `${structureCount} structures` : 'Whole surface'].filter(Boolean).join(', ');
  const segmenter = m && !atlas ? `${m.segmentation.model === 'totalsegmentator' ? 'TotalSegmentator' : m.segmentation.model} ${m.segmentation.model_version}` : null;

  const cameraLabel = !handsOn ? 'Hands off' : camera === 'on' ? 'Hands on' : camera === 'starting' ? 'Starting camera' : camera === 'blocked' ? 'Camera blocked' : 'No camera';

  return (
    <div className="hud-container">
      <header className="hud-top-bar">
        <div className="hud-logo">
          <Link to={backTo} className="hud-back hud-interactive" aria-label="Back to Vesalius">
            <ArrowLeft size={15} />
          </Link>
          <Stack size={19} weight="fill" className="hud-brand-mark" aria-hidden="true" />
          <span className="logo-text">Vesalius</span>
          <span className="hud-scene-title" data-testid="scene-title">{title}</span>
        </div>

        <div className={`hud-status-pill hud-status-pill--${statusTone}`} data-testid="gesture-status">
          {status.icon}<span>{status.label}</span>
          {hands && ignoredHands > 0 && (
            <span className="hud-following" data-testid="following-you"
              title={`${ignoredHands} other ${ignoredHands === 1 ? 'hand' : 'hands'} in view, ignored`}>Following you</span>
          )}
        </div>

        <div className="hud-top-right">
          <button
            className={`hud-chip hud-interactive ${handsOn && camera === 'on' ? 'hud-chip--on' : ''}`}
            onClick={onToggleHands}
            title={handsOn ? 'Turn hand tracking off (mouse only)' : 'Turn hand tracking on'}
            data-testid="hands-toggle"
            aria-label={cameraLabel}
          >
            {handsOn && camera === 'on' ? <Camera size={14} /> : <CameraSlash size={14} />}
            <span className="hud-chip__label">{cameraLabel}</span>
          </button>
          {!atlas && (current?.summary.source === 'pipeline' || current?.summary.source === 'static') && (
            <button className="hud-chip hud-interactive" onClick={() => setShowAudit((v) => !v)} data-testid="audit-toggle" aria-label="Audit trail">
              <TreeStructure size={14} /> <span className="hud-chip__label">Audit trail</span>
            </button>
          )}
          <button className="hud-chip hud-interactive" onClick={onToggleFullscreen} data-testid="fullscreen-toggle" aria-label={fullscreen ? 'Exit full screen' : 'Full screen'}
            title={fullscreen ? 'Exit full screen (F)' : 'Full screen (F)'}>
            {fullscreen ? <CornersIn size={14} /> : <CornersOut size={14} />}
            <span className="hud-chip__label">{fullscreen ? 'Exit' : 'Full screen'}</span>
          </button>
        </div>
      </header>

      {/* one panel on the left: what this is, its systems, and the scan's details on request */}
      <aside className="room-panel hud-interactive" data-testid="study-facts">
        <div className="room-panel__head">
          <h1 className="room-panel__title">{title}</h1>
          <p className="room-panel__summary">{summary}</p>
          {m && !atlas && m.deface.applied && (
            <span className="room-badge"><ShieldCheck size={13} weight="fill" /> Face removed</span>
          )}
        </div>
        <LayerPanel />
        {m && (
          <details className="room-details">
            <summary>{atlas ? 'About this atlas' : 'Scan details'}</summary>
            {atlas ? (
              <>
                <Row k="Source" v="Z-Anatomy, BodyParts3D" />
                <Row k="Licence" v="CC-BY-SA 4.0" />
                <Row k="Systems" v={m.groups.length} />
              </>
            ) : (
              <>
                <Row k="Body part" v={m.study.body_part ?? 'Unknown'} />
                <Row k="Slices" v={m.study.slice_count ?? 'Unknown'} />
                <Row k="Voxel" v={spacing ? <>{spacing} <em>mm</em></> : 'Unknown'} />
                <Row k="Face" v={m.deface.applied ? 'Removed' : m.deface.required ? 'Required' : 'Not a head'} tone={m.deface.applied ? 'ok' : undefined} />
                {m.study.shifted_study_date && <Row k="Date" v={<>{m.study.shifted_study_date} <em>shifted</em></>} />}
                {segmenter && <Row k="Segmenter" v={segmenter} />}
              </>
            )}
            <Row k="Contract" v={`mesh_contract ${m.contract_version}`} />
          </details>
        )}
      </aside>

      {/* the mouse's hover label: the landing's callout chip, beside the pointer */}
      <div ref={hoverChip} className="hover-chip" aria-hidden="true">
        {!hands && hoverName && <span className="hover-chip__label">{hoverName}</span>}
      </div>

      {/* the hand's cursor: a ring at the index finger; it fills over a part and names it */}
      {hands && cursor && (
        <div
          className={`hand-cursor${hoverName ? ' hand-cursor--hover' : ''}${pose === 'pinch' ? ' hand-cursor--pinch' : ''}${dwellProgress > 0 ? ' hand-cursor--holding' : ''}`}
          style={{
            transform: `translate3d(${(cursor.x * 100).toFixed(2)}vw, ${(cursor.y * 100).toFixed(2)}vh, 0)`,
            ['--hold' as string]: dwellProgress.toFixed(3),
          }}
          aria-hidden="true"
          data-testid="hand-cursor"
        >
          <span className="hand-cursor__ring" />
          <span className="hand-cursor__hold" />
          {hoverName && <span className="hand-cursor__label">{hoverName}</span>}
        </div>
      )}

      {/* the sweeping palm, once something is selected: dashed while open, a ring filling as it
          holds still, solid mint once armed. It shows which step a sweep is waiting for. */}
      {hands && selectedMesh && sweep?.open && (
        <div
          className={`palm-marker${sweepArmed ? ' palm-marker--armed' : ''}${sweeping ? ' palm-marker--sweeping' : ''}`}
          style={{
            transform: `translate3d(${(sweep.at.x * 100).toFixed(2)}vw, ${(sweep.at.y * 100).toFixed(2)}vh, 0)`,
            ['--arm' as string]: sweepArming.toFixed(3),
          }}
          aria-hidden="true"
          data-testid="palm-marker"
        >
          <HandPalm size={20} />
        </div>
      )}

      {flash && <div className="hud-flash" role="status" data-testid="hud-flash">{flash}</div>}

      {showAudit && current && <AuditDrawer studyId={current.summary.id} onClose={() => setShowAudit(false)} />}

      <footer className="hud-bottom">
        {m && current && (
          <div className="hud-provenance hud-interactive" data-testid="provenance">
            {atlas
              ? <>Atlas: Z-Anatomy / BodyParts3D, CC-BY-SA · parts under other licences removed</>
              : <>{current.summary.credit ? `${current.summary.credit} · ` : ''}pipeline {m.pipeline_version} · series {m.series_id}</>}
          </div>
        )}

        <p className="hud-hint" role="status" data-testid="gesture-hint">{hint}</p>

        {partsRemoved && onRestore && (
          <button className="revert-btn hud-interactive" onClick={onRestore} title="Restore anatomy">
            <ArrowCounterClockwise size={14} /> Restore anatomy
          </button>
        )}

        {guideOpen && <HandGuide atlas={atlas} onClose={closeGuide} />}

        <button id="how-to-use-btn" className="how-to-use-btn hud-interactive" onClick={() => (guideOpen ? closeGuide() : setGuide('open'))}>
          {guideOpen ? <X size={13} /> : <Question size={13} />}
          {guideOpen ? 'Close' : 'How to use'}
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

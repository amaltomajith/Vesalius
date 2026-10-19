import { useEffect, useState } from 'react';
import { ArrowsOut, FrameCorners, X } from '@phosphor-icons/react';
import { getAnatomyInfo } from '../data/anatomyDatabase';
import { DISCLAIMER, measurementContext } from '../data/measurementContext';
import { useStudyStore } from '../store/studyStore';
import { GROUP_COLOURS } from '../store/layerStore';
import { splitDescription } from '../lib/description';
import AiInsight from './AiInsight';

interface PartInfoPanelProps {
  selectedMesh: string | null;
  onClose: () => void;
  /** the selected part is shown on its own */
  isolated: boolean;
  onToggleIsolate: () => void;
  /** atlas only: the latest framebuffer snapshot, for the optional vision insight */
  snapshot: string | null;
}

const fmt = (n: number | undefined, d = 1) =>
  n === undefined ? '·' : n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

export default function PartInfoPanel({ selectedMesh, onClose, isolated, onToggleIsolate, snapshot }: PartInfoPanelProps) {
  const descriptions = useStudyStore((s) => s.descriptions);
  const current = useStudyStore((s) => s.current);
  const loadDescriptions = useStudyStore((s) => s.loadDescriptions);
  const atlas = current?.manifest.kind === 'atlas';
  // "Read more" starts closed for each structure
  const [more, setMore] = useState<{ node: string | null; open: boolean }>({ node: null, open: false });
  const readMore = more.node === selectedMesh && more.open;

  useEffect(() => {
    if (selectedMesh && atlas) void loadDescriptions();
  }, [selectedMesh, atlas, loadDescriptions]);

  if (!selectedMesh) {
    return <div className="part-info-panel" aria-hidden="true" />;
  }

  // re-read on every render: descriptions arrive asynchronously
  void descriptions;
  const info = getAnatomyInfo(selectedMesh);
  const m = info.measurements;
  const faceCut = !!current?.manifest.deface.structures_cut;

  const swatch = m?.groupId ? GROUP_COLOURS[m.groupId]?.color : undefined;
  const text = info.description ? splitDescription(info.description) : null;

  return (
    <div className="part-info-panel part-info-panel--visible hud-interactive" data-testid="part-info">
      <div className="part-info-panel__header">
        <div>
          <span className="part-info-panel__system" style={{ '--sys-color': swatch ?? 'var(--room-ink-3)' } as React.CSSProperties}>
            <i aria-hidden="true" />{info.system}
          </span>
          <h2 className="part-info-panel__title">{info.displayName}</h2>
          {info.path.length > 0 && <div className="part-info-panel__node">{info.path.join(' / ')}</div>}
          {!atlas && <div className="part-info-panel__node mono">{info.node}</div>}
        </div>
        <button className="part-info-panel__close-btn" onClick={onClose} title="Dismiss" aria-label="Close">
          <X size={15} />
        </button>
      </div>

      <div className="part-info-panel__actions">
        <button type="button" className={`ai-run part-action${isolated ? ' part-action--on' : ''}`} onClick={onToggleIsolate}
          aria-pressed={isolated} data-testid="isolate-toggle">
          {isolated ? <ArrowsOut size={14} /> : <FrameCorners size={14} />} {isolated ? 'Show all' : 'Isolate'}
        </button>
        <span className="part-action__hint">{isolated ? 'or sweep an open palm sideways, or click empty space' : 'or sweep an open palm sideways, or click it again'}</span>
      </div>

      {atlas ? (
        <>
          <div className="part-info-panel__section" data-testid="atlas-description">
            {text && text.lead ? (
              <>
                <p className="part-info-panel__lead">{text.lead}</p>
                {readMore && text.rest.map((para, i) => <p key={i} className="part-info-panel__section-text">{para}</p>)}
                {text.rest.length > 0 && (
                  <button type="button" className="part-info-panel__more"
                    onClick={() => setMore({ node: selectedMesh, open: !readMore })} aria-expanded={readMore}>
                    {readMore ? 'Show less' : 'Read more'}
                  </button>
                )}
              </>
            ) : (
              <p className="part-info-panel__section-text">
                {descriptions ? 'No description for this structure in the atlas.' : 'Loading the description'}
              </p>
            )}
          </div>
          <p className="part-info-panel__credit">
            Anatomy: Z-Anatomy / BodyParts3D (CC-BY-SA). Text: Wikipedia via Z-Anatomy (CC-BY-SA).
          </p>
          <AiInsight kind="atlas" node={selectedMesh} label={info.displayName} snapshot={snapshot} />
        </>
      ) : (
        <>
          {m && (
            <dl className="measure-grid" data-testid="measurements">
              <div><dt>Volume</dt><dd>{fmt(m.volume_ml)} <em>ml</em></dd></div>
              <div><dt>Mean</dt><dd>{fmt(m.hu_mean)} <em>HU</em></dd></div>
              <div><dt>Spread</dt><dd>&plusmn;{fmt(m.hu_std)} <em>HU</em></dd></div>
              <div><dt>Mesh</dt><dd>{m.faces.toLocaleString()} <em>tris</em></dd></div>
            </dl>
          )}

          <div className="part-info-panel__section">
            <h3 className="part-info-panel__section-title">Function</h3>
            <p className="part-info-panel__section-text">{info.function}</p>
          </div>
          <div className="part-info-panel__section">
            <h3 className="part-info-panel__section-title">Location</h3>
            <p className="part-info-panel__section-text">{info.location}</p>
          </div>

          <div className="part-info-panel__divider" />

          <div className="ai-insight-box">
            <div className="ai-insight-box__header">
              <span className="ai-insight-box__title">Measurements &amp; context</span>
              <span className="ai-insight-box__status">From the pipeline</span>
            </div>
            <ul className="context-list">
              {measurementContext(info, faceCut).map((l) => (
                <li key={l.key}>{l.text}</li>
              ))}
              {info.clinicalNote && <li key="note">{info.clinicalNote}</li>}
            </ul>
            <div className="context-disclaimer">{DISCLAIMER}</div>
          </div>
          {current && (current.summary.source === 'pipeline' || current.summary.source === 'static') && (
            <AiInsight kind="study" node={selectedMesh} label={info.displayName} studyId={current.summary.id} snapshot={null} />
          )}
        </>
      )}
    </div>
  );
}

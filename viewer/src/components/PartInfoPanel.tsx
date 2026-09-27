import { useEffect } from 'react';
import { X } from '@phosphor-icons/react';
import { getAnatomyInfo } from '../data/anatomyDatabase';
import { DISCLAIMER, measurementContext } from '../data/measurementContext';
import { useStudyStore } from '../store/studyStore';
import AiInsight from './AiInsight';

interface PartInfoPanelProps {
  selectedMesh: string | null;
  onClose: () => void;
  leftFistActive?: boolean;
  /** atlas only: the latest framebuffer snapshot, for the optional vision insight */
  snapshot: string | null;
}

const fmt = (n: number | undefined, d = 1) =>
  n === undefined ? '·' : n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });

export default function PartInfoPanel({ selectedMesh, onClose, leftFistActive, snapshot }: PartInfoPanelProps) {
  const descriptions = useStudyStore((s) => s.descriptions);
  const current = useStudyStore((s) => s.current);
  const loadDescriptions = useStudyStore((s) => s.loadDescriptions);
  const atlas = current?.manifest.kind === 'atlas';

  // Dismiss if left fist gesture is detected
  useEffect(() => {
    if (selectedMesh && leftFistActive) {
      onClose();
    }
  }, [leftFistActive, selectedMesh, onClose]);

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

  return (
    <div className="part-info-panel part-info-panel--visible hud-interactive" data-testid="part-info">
      <div className="part-info-panel__header">
        <div>
          <h2 className="part-info-panel__title">{info.displayName}</h2>
          <span className="part-info-panel__system-badge">{info.system}</span>
          {info.path.length > 0 && <div className="part-info-panel__node">{info.path.join(' / ')}</div>}
          {!atlas && <div className="part-info-panel__node">{info.node}</div>}
        </div>
        <button className="part-info-panel__close-btn" onClick={onClose} title="Dismiss" aria-label="Close">
          <X size={16} weight="bold" />
        </button>
      </div>

      {atlas ? (
        <>
          <div className="part-info-panel__divider" />
          <div className="part-info-panel__section" data-testid="atlas-description">
            <h3 className="part-info-panel__section-title">About</h3>
            {info.description ? (
              info.description.split('\n\n').map((para, i) => (
                <p key={i} className="part-info-panel__section-text">{para}</p>
              ))
            ) : (
              <p className="part-info-panel__section-text">
                {descriptions ? 'No description for this structure in the atlas.' : 'Loading the description…'}
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

          <div className="part-info-panel__divider" />

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
              <span className="ai-insight-box__title">MEASUREMENTS &amp; CONTEXT</span>
              <span className="ai-insight-box__status">FROM PIPELINE</span>
            </div>
            <ul className="context-list">
              {measurementContext(info, faceCut).map((l) => (
                <li key={l.key}>{l.text}</li>
              ))}
              {info.clinicalNote && <li key="note">{info.clinicalNote}</li>}
            </ul>
            <div className="context-disclaimer">{DISCLAIMER}</div>
          </div>
          {current && current.summary.source === 'pipeline' && (
            <AiInsight kind="study" node={selectedMesh} label={info.displayName} studyId={current.summary.id} snapshot={null} />
          )}
        </>
      )}
    </div>
  );
}

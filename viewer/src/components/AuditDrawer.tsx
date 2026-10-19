import { useEffect, useState } from 'react';
import { X } from '@phosphor-icons/react';
import { readUrl } from '../lib/runtime';

interface LineageNode {
  stage: string;
  stage_version: string;
  run_id?: string;
  status?: string;
}

const STAGE_NAMES: Record<string, string> = {
  S1: 'Ingest', S2: 'Index', S3: 'Quality gate', S4: 'De-identify', S5: 'Verify', S6: 'Volume',
  S7: 'Deface', S8: 'Segment', S9: 'Surface', S10: 'Mesh', S11: 'Export', S12: 'Serve',
};

/** The study's lineage (GET /studies/{id}/lineage): which stage runs, at which versions, made
 *  what is on screen. Lineage made visible in the product. */
export default function AuditDrawer({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const [nodes, setNodes] = useState<LineageNode[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(readUrl(`/studies/${studyId}/lineage`)) // the static site: the exported lineage.json
      .then(async (r) => {
        if (!r.ok) throw new Error(`lineage unavailable (${r.status})`);
        return (await r.json()) as { nodes: LineageNode[] };
      })
      .then((body) => {
        if (!live) return;
        const seen = new Set<string>();
        const ordered = body.nodes
          .slice()
          .sort((a, b) => Number(a.stage.slice(1)) - Number(b.stage.slice(1)))
          .filter((n) => (seen.has(n.stage) ? false : (seen.add(n.stage), true)));
        setNodes(ordered);
      })
      .catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [studyId]);

  return (
    <aside className="audit-drawer hud-interactive" data-testid="audit-drawer" aria-label="Audit trail">
      <div className="audit-drawer__head">
        <div className="panel-label">Audit trail</div>
        <button className="part-info-panel__close-btn" onClick={onClose} aria-label="Close"><X size={14} /></button>
      </div>
      <p className="audit-drawer__lede">Every stage that produced this model, with the version that ran.</p>
      {error && <p className="audit-drawer__error">{error}</p>}
      {!nodes && !error && <p className="audit-drawer__lede">Loading…</p>}
      {nodes && (
        <ol className="audit-drawer__list">
          {nodes.map((n) => (
            <li key={n.stage}>
              <span className="mono">{n.stage}</span>
              <span>{STAGE_NAMES[n.stage] ?? n.stage}</span>
              <span className="mono">v{n.stage_version}</span>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { ArrowRight, PersonArmsSpread } from '@phosphor-icons/react';
import { useStudyStore } from '../store/studyStore';
import Thumb from '../components/Thumb';

const FILTERS = ['All', 'HEAD', 'CHEST', 'ABDOMEN'] as const;

/** Every study the pipeline serves from gold (GET /studies), plus the bundled demo. */
export default function Studies() {
  const studies = useStudyStore((s) => s.studies);
  const refresh = useStudyStore((s) => s.refresh);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('All');

  useEffect(() => { void refresh(); }, [refresh]);

  const shown = useMemo(
    () => (studies ?? []).filter((s) => filter === 'All' || s.body_part === filter),
    [studies, filter],
  );

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="kicker mono">Studies</p>
          <h1>Every published 3D study</h1>
          <p className="lede">
            Each one came out of the gold zone: de-identified, verified and, for heads, defaced before
            any surface was built. Open one to explore it full screen, with your hands or the mouse.
          </p>
        </div>
      </header>

      <div className="seg-filter" role="group" aria-label="Body part">
        {FILTERS.map((f) => (
          <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === 'All' ? 'All' : f.charAt(0) + f.slice(1).toLowerCase()}
          </button>
        ))}
      </div>

      {studies === null && <div className="card-grid">{[0, 1, 2].map((i) => <div key={i} className="card skeleton" />)}</div>}

      {studies && (
        <div className="card-grid" data-testid="study-grid">
          {shown.map((s) => (
            <Link key={`${s.source}:${s.id}`} className="card study-card" to={`/hologram/${s.id}`} data-testid={`study-${s.source}`} data-study-id={s.id}>
              <Thumb study={s} className="study-card__thumb" />
              <div className="study-card__body">
                <div className="study-card__title">
                  {s.modality ?? 'CT'} · {s.body_part ? s.body_part.charAt(0) + s.body_part.slice(1).toLowerCase() : 'Unknown region'}
                  {s.source === 'demo' && <span className="chip">bundled</span>}
                </div>
                <div className="study-card__meta">{s.slice_count ? `${s.slice_count} slices` : ''}</div>
                <div className="study-card__id mono">{s.source === 'demo' ? s.credit : s.id}</div>
              </div>
              <ArrowRight className="study-card__go" size={18} />
            </Link>
          ))}
          <Link className="card study-card study-card--atlas" to="/body">
            <div className="study-card__thumb study-card__thumb--atlas"><PersonArmsSpread size={30} /></div>
            <div className="study-card__body">
              <div className="study-card__title">Full body atlas</div>
              <div className="study-card__meta">8 systems, full detail</div>
              <div className="study-card__id mono">Z-Anatomy, CC-BY-SA</div>
            </div>
            <ArrowRight className="study-card__go" size={18} />
          </Link>
          {shown.length === 0 && (
            <div className="empty">
              <p>No {filter.toLowerCase()} study yet.</p>
              <Link className="btn btn-sm" to="/pipeline">Run a scan</Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

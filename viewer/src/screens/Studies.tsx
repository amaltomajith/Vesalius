import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { ArrowRight, Cube, PersonArmsSpread, Play } from '@phosphor-icons/react';
import { useStudyStats } from '../lib/useStudyStats';
import { SYSTEMS } from '../lib/stats';
import Thumb from '../components/Thumb';

const n = new Intl.NumberFormat('en-GB');
const title = (s: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : 'Unknown region');

/** Every study the pipeline serves from gold (GET /studies), plus the bundled demo and the atlas.
 *  The counts on each card come from its structures manifest, validated first (lib/stats.ts). */
export default function Studies() {
  const { studies, served, summary } = useStudyStats();
  const [filter, setFilter] = useState<string>('All');

  // the filters are the body parts actually served, not a fixed list
  const parts = useMemo(() => [...new Set((studies ?? []).map((s) => s.body_part).filter((p): p is string => !!p))], [studies]);
  const shown = useMemo(
    () => (studies ?? []).filter((s) => filter === 'All' || s.body_part === filter),
    [studies, filter],
  );
  const statsFor = (id: string) => summary?.studies.find((s) => s.id === id);

  return (
    <div className="page page--dash">
      <section className="tile tile--wide page-tile" aria-labelledby="studies-h">
        <div className="page-tile__copy">
          <h1 id="studies-h">Studies</h1>
          <p className="big-num">{studies ? served.length : '0'}<span>served from gold</span></p>
          <p className="tile-note">
            Each one was de-identified, verified and, for heads, had its face cut before any surface was built.
            Open one to explore it full screen, with your hands or the mouse.
          </p>
        </div>
        {parts.length > 1 && (
          <div className="seg-pill" role="group" aria-label="Body part">
            {['All', ...parts].map((f) => (
              <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f === 'All' ? 'All' : title(f)}
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="study-grid" data-testid="study-grid">
        {studies === null && [0, 1, 2].map((i) => <div key={i} className="tile tile-skeleton tile-skeleton--study" />)}

        {shown.map((s) => {
          const st = statsFor(s.id);
          const systems = st ? SYSTEMS.filter((k) => st.perSystem[k] > 0).length : null;
          return (
            <Link key={`${s.source}:${s.id}`} className="tile study-tile" to={`/hologram/${s.id}`} data-testid={`study-${s.source}`} data-study-id={s.id}>
              <Thumb study={s} className="study-tile__img" />
              <div className="study-tile__head">
                <h2>{s.modality ?? 'CT'} {title(s.body_part)}</h2>
                {st?.defaceApplied && <span className="chip">face cut</span>}
              </div>
              <dl className="facts">
                <div><dt>Slices</dt><dd>{s.slice_count ?? '?'}</dd></div>
                <div><dt>Structures</dt><dd>{st ? st.total : '…'}</dd></div>
                <div><dt>Systems</dt><dd>{systems ?? '…'}</dd></div>
              </dl>
              <p className="study-tile__largest">
                {st?.largest ? <>Largest: <b>{st.largest.label}</b>, {n.format(Math.round(st.largest.ml))} ml</> : ' '}
              </p>
              {s.credit && <p className="study-tile__credit">{s.credit}</p>}
              <span className="study-tile__foot">
                <span className="mono study-tile__id">{s.id}</span>
                <span className="study-tile__open"><Cube size={15} /> Open in 3D <ArrowRight size={14} /></span>
              </span>
            </Link>
          );
        })}

        {studies && filter === 'All' && (
          <Link className="tile study-tile study-tile--atlas" to="/body">
            <span className="study-tile__img study-tile__img--atlas"><PersonArmsSpread size={44} /></span>
            <div className="study-tile__head"><h2>Full body atlas</h2></div>
            <dl className="facts">
              <div><dt>Systems</dt><dd>8</dd></div>
              <div><dt>Detail</dt><dd>Full</dd></div>
              <div><dt>Control</dt><dd>Hands</dd></div>
            </dl>
            <p className="study-tile__largest">Z-Anatomy, CC-BY-SA. Not a patient: a reference body.</p>
            <span className="study-tile__foot">
              <span className="mono study-tile__id">assets/atlas</span>
              <span className="study-tile__open"><Cube size={15} /> Open in 3D <ArrowRight size={14} /></span>
            </span>
          </Link>
        )}

        {studies && shown.length === 0 && (
          <div className="tile tile-empty">
            <p className="tile-empty__title">No {filter === 'All' ? '' : title(filter).toLowerCase() + ' '}study yet</p>
            <p>Run a scan and its 3D study appears here once it is published.</p>
            <Link className="btn btn-primary btn-sm" to="/pipeline"><Play size={14} weight="fill" /> Run a scan</Link>
          </div>
        )}
      </div>
    </div>
  );
}

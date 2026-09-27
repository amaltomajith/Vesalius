import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { ArrowRight, PersonArmsSpread, Play, ShieldCheck, Stack } from '@phosphor-icons/react';
import { api, type Catalogue, type ProofJob } from '../lib/api';
import { useStudyStore } from '../store/studyStore';
import Thumb from '../components/Thumb';

/** Phantom's front page inside the app: what the pipeline has published, what it has proven, and
 *  the three things to do next. */
export default function Dashboard() {
  const studies = useStudyStore((s) => s.studies);
  const refresh = useStudyStore((s) => s.refresh);
  const [info, setInfo] = useState<Catalogue | null>(null);
  const [last, setLast] = useState<Record<string, ProofJob>>({});

  useEffect(() => {
    void refresh();
    void api<Catalogue>('/demo/datasets').then(setInfo).catch(() => setInfo(null));
    void api<Record<string, ProofJob>>('/demo/proofs/last').then(setLast).catch(() => setLast({}));
  }, [refresh]);

  const served = (studies ?? []).filter((s) => s.source === 'pipeline');
  const proofs = info?.proofs ?? [];
  const passed = proofs.filter((p) => last[p.key]?.verdict === 'passed').length;

  return (
    <div className="page page--dash">
      <header className="dash-hero">
        <div className="dash-hero__copy">
          <p className="kicker mono">Phantom</p>
          <h1>Scans in. Safe 3D out. <span className="accent">Proven.</span></h1>
          <p className="lede">
            Every scan is de-identified across tags, private data and pixels, the face is removed before
            any surface exists, and each 3D model carries its full lineage. Explore any result full
            screen, with your hands.
          </p>
          <div className="chip-row">
            <Link className="btn btn-primary" to="/pipeline"><Play size={15} weight="fill" /> Run a scan</Link>
            <Link className="btn" to="/body"><PersonArmsSpread size={16} /> Explore the full body</Link>
          </div>
        </div>
        <dl className="dash-stats" data-testid="dash-stats">
          <div><dt>Studies published</dt><dd>{studies ? served.length : '·'}</dd></div>
          <div><dt>Proofs passing</dt><dd>{info ? `${passed} / ${proofs.length}` : '·'}</dd></div>
          <div>
            <dt>Pipeline</dt>
            <dd className={info?.busy ? 'live' : ''}>{info ? (info.busy ? 'Working' : 'Idle') : 'Offline'}</dd>
          </div>
        </dl>
      </header>

      <section className="dash-section">
        <div className="dash-section__head">
          <h2><Stack size={18} /> Recent studies</h2>
          <Link to="/studies" className="link-more">All studies <ArrowRight size={14} /></Link>
        </div>
        <div className="card-grid">
          {studies === null && [0, 1, 2].map((i) => <div key={i} className="card skeleton" />)}
          {studies?.slice(0, 5).map((s) => (
            <Link key={`${s.source}:${s.id}`} to={`/hologram/${s.id}`} className="card study-card">
              <Thumb study={s} className="study-card__thumb" />
              <div className="study-card__body">
                <div className="study-card__title">
                  {s.modality ?? 'CT'} · {s.body_part ? s.body_part.charAt(0) + s.body_part.slice(1).toLowerCase() : 'Unknown'}
                  {s.source === 'demo' && <span className="chip">bundled</span>}
                </div>
                <div className="study-card__meta">{s.slice_count ? `${s.slice_count} slices` : ''}</div>
              </div>
              <ArrowRight className="study-card__go" size={18} />
            </Link>
          ))}
          <Link className="card study-card study-card--atlas" to="/body">
            <div className="study-card__thumb study-card__thumb--atlas"><PersonArmsSpread size={30} /></div>
            <div className="study-card__body">
              <div className="study-card__title">Full body atlas</div>
              <div className="study-card__meta">8 systems, hands-controlled</div>
            </div>
            <ArrowRight className="study-card__go" size={18} />
          </Link>
        </div>
      </section>

      <section className="dash-section">
        <div className="dash-section__head">
          <h2><ShieldCheck size={18} /> Proofs</h2>
          <Link to="/proofs" className="link-more">Run them <ArrowRight size={14} /></Link>
        </div>
        <ol className="dash-proofs">
          {proofs.map((p) => {
            const v = last[p.key]?.state === 'finished' ? last[p.key].verdict : null;
            return (
              <li key={p.key}>
                <span className="mono">{String(p.number).padStart(2, '0')}</span>
                <span>{p.title}</span>
                <span className={`chip ${v === 'passed' ? 'pass' : v ? 'reject' : ''}`}>{v ?? 'not run yet'}</span>
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}

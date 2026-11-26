import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import {
  ArrowRight, CheckCircle, CircleDashed, Cube, EyeSlash, Play, PersonArmsSpread, ShieldCheck, Stack,
  TreeStructure, UploadSimple, XCircle,
} from '@phosphor-icons/react';
import { api, type Catalogue, type ProofJob } from '../lib/api';
import { bySystem, totals } from '../lib/stats';
import { useStudyStats } from '../lib/useStudyStats';
import Thumb from '../components/Thumb';
import SystemBars from '../components/SystemBars';

const n = new Intl.NumberFormat('en-GB');
const title = (s: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : 'Unknown');

/** The app's front page: what the AI has named in the served studies, the way to run the next
 *  scan, what the privacy guarantees did, what the proofs say, and the studies themselves.
 *  Every number comes from the pipeline's API; nothing here is a placeholder. */
export default function Dashboard() {
  const navigate = useNavigate();
  const { studies, served, summary } = useStudyStats();
  const [info, setInfo] = useState<Catalogue | null | 'offline'>(null);
  const [last, setLast] = useState<Record<string, ProofJob>>({});
  const [only, setOnly] = useState<string | null>(null);
  const [mode, setMode] = useState<'sample' | 'upload'>('sample');
  const [pick, setPick] = useState<string | null>(null);

  useEffect(() => {
    void api<Catalogue>('/demo/datasets').then(setInfo).catch(() => setInfo('offline'));
    void api<Record<string, ProofJob>>('/demo/proofs/last').then(setLast).catch(() => setLast({}));
  }, []);

  const catalogue = info && info !== 'offline' ? info : null;
  const datasets = catalogue?.datasets ?? [];
  const chosen = pick ?? datasets[0]?.key ?? null;
  const proofs = catalogue?.proofs ?? [];
  const verdict = (key: string) => (last[key]?.state === 'finished' ? last[key].verdict : null);
  const passed = proofs.filter((p) => verdict(p.key) === 'passed').length;

  const named = summary ? totals(summary.studies, only) : null;
  const all = summary ? totals(summary.studies) : null;
  const slices = served.filter((s) => !only || s.id === only).reduce((k, s) => k + (s.slice_count ?? 0), 0);
  const statsFor = (id: string) => summary?.studies.find((s) => s.id === id);
  // the evidence for the privacy card: a served study whose face the pipeline cut
  const headStudy = served.find((s) => statsFor(s.id)?.defaceApplied);
  const loading = summary === null;

  return (
    <div className="page page--dash">
      <div className="bento">
        {/* ---- what the AI named ---- */}
        <section className="tile tile--named" aria-labelledby="named-h">
          <header className="tile__head">
            <h2 id="named-h"><TreeStructure size={18} /> Anatomy named by AI</h2>
            {summary && summary.studies.length > 1 && (
              <div className="seg-pill" role="group" aria-label="Which studies">
                <button type="button" aria-pressed={only === null} onClick={() => setOnly(null)}>All</button>
                {summary.studies.map((s) => (
                  <button key={s.id} type="button" aria-pressed={only === s.id} onClick={() => setOnly(s.id)}>{s.bodyPart}</button>
                ))}
              </div>
            )}
          </header>

          {loading ? (
            <div className="tile-skeleton tile-skeleton--chart" />
          ) : summary.studies.length === 0 ? (
            <div className="tile-empty">
              <p className="tile-empty__title">No studies yet</p>
              <p>Run a scan and every structure the model finds is named, measured and counted here.</p>
              <Link className="btn btn-primary btn-sm" to="/pipeline"><Play size={14} weight="fill" /> Run a scan</Link>
            </div>
          ) : (
            <div className="named">
              <div className="named__figure">
                <p className="big-num">{n.format(named!.structures)}<span>structures</span></p>
                <p className="named__sub">
                  named in {named!.studies === 1
                    ? `the ${(summary.studies.find((s) => s.id === (only ?? summary.studies[0].id))?.bodyPart ?? '').toLowerCase()} study`
                    : `${named!.studies} studies`}, each one meshed and measured
                </p>
                <SystemBars rows={bySystem(summary.studies, only)} caption="Structures per body system" />
              </div>
              <aside className="named__side">
                <div className="inset">
                  <p className="inset__label">Largest structure</p>
                  <p className="inset__value">{named!.largest ? named!.largest.label : 'None measured'}</p>
                  {named!.largest && <p className="inset__meta mono">{n.format(Math.round(named!.largest.ml))} ml</p>}
                </div>
                <div className="inset">
                  <p className="inset__label">Slices read</p>
                  <p className="inset__value">{n.format(slices)}</p>
                  <p className="inset__meta">DICOM slices behind {only ? 'this study' : 'these studies'}</p>
                </div>
                <Link className="btn btn-dark btn-sm" to={only ? `/hologram/${only}` : '/studies'}>
                  <Cube size={15} /> {only ? 'Open in 3D' : 'All studies'}
                </Link>
              </aside>
            </div>
          )}
        </section>

        {/* ---- the next scan ---- */}
        <section className="tile tile--run" aria-labelledby="run-h">
          <header className="tile__head"><h2 id="run-h"><Play size={17} /> Run a scan</h2></header>
          <div className="seg-wide" role="tablist" aria-label="Source">
            <button type="button" role="tab" aria-selected={mode === 'sample'} onClick={() => setMode('sample')}>Sample scan</button>
            <button type="button" role="tab" aria-selected={mode === 'upload'} onClick={() => setMode('upload')}>Your file</button>
          </div>
          {mode === 'sample' ? (
            <>
              <div className="pick-list" role="radiogroup" aria-label="Sample scans">
                {info === null && [0, 1, 2].map((i) => <div key={i} className="tile-skeleton tile-skeleton--row" />)}
                {info === 'offline' && <p className="tile-note">Cannot reach the pipeline. Is it still running?</p>}
                {datasets.map((d) => (
                  <button key={d.key} type="button" role="radio" aria-checked={chosen === d.key} className="pick" onClick={() => setPick(d.key)}>
                    <span className="pick__dot" aria-hidden="true" />
                    <span className="pick__text">
                      <b>{d.title}</b>
                      <small>{d.synthetic ? 'Synthetic' : 'Public test file'}, about {d.approx_seconds} s</small>
                    </span>
                    {d.expected === 'rejected' && <span className="chip reject">Rejected by design</span>}
                  </button>
                ))}
              </div>
              <button type="button" className="btn btn-primary btn-block" disabled={!chosen || catalogue?.busy}
                onClick={() => chosen && navigate(`/pipeline?dataset=${encodeURIComponent(chosen)}`)}>
                <Play size={15} weight="fill" /> {catalogue?.busy ? 'The pipeline is busy' : 'Set up this run'}
              </button>
            </>
          ) : (
            <>
              <p className="tile-note">
                A zip of DICOM files{catalogue ? `, up to ${catalogue.upload.max_megabytes} MB` : ''}. It is checked, stripped of
                identity and rebuilt on this machine; nothing leaves it.
              </p>
              <Link className="btn btn-primary btn-block" to="/pipeline?dataset=upload"><UploadSimple size={15} /> Choose a file</Link>
            </>
          )}
        </section>

        {/* ---- privacy, measured ---- */}
        <section className="tile" aria-labelledby="privacy-h">
          <header className="tile__head"><h2 id="privacy-h"><EyeSlash size={17} /> Faces removed</h2></header>
          {loading ? <div className="tile-skeleton" /> : (
            <>
              <p className="big-num">
                {all!.defaceRequired === 0 ? '0' : `${all!.defaceApplied} / ${all!.defaceRequired}`}
                <span>{all!.defaceRequired === 1 ? 'head study' : 'head studies'}</span>
              </p>
              <p className="tile-sub">
                {all!.defaceRequired === 0 ? 'No head study yet: none has needed its face cut.' : 'had the face cut before any 3D surface existed'}
              </p>
              {headStudy && (
                <Link to={`/hologram/${headStudy.id}`} className="evidence">
                  <Thumb study={headStudy} className="evidence__img" />
                  <span className="evidence__cap">
                    <b>The served head, as stored</b>
                    <small>A slice of the gold volume, after the cut</small>
                  </span>
                </Link>
              )}
              <div className="inset-pair">
                <div className="inset">
                  <p className="inset__label">Served, gold only</p>
                  <p className="inset__value">{summary.studies.length}</p>
                </div>
                <div className="inset">
                  <p className="inset__label">Refused by the viewer</p>
                  <p className="inset__value">{summary.refused.length}</p>
                </div>
              </div>
            </>
          )}
        </section>

        {/* ---- proofs ---- */}
        <section className="tile" aria-labelledby="proofs-h">
          <header className="tile__head">
            <h2 id="proofs-h"><ShieldCheck size={17} /> Proofs</h2>
            <Link to="/proofs" className="tile__link">Run them <ArrowRight size={13} /></Link>
          </header>
          <p className="big-num">{catalogue ? `${passed} / ${proofs.length}` : '0'}<span>passing on this machine</span></p>
          <ol className="proof-rows">
            {proofs.map((p) => {
              const v = verdict(p.key);
              const Icon = v === 'passed' ? CheckCircle : v ? XCircle : CircleDashed;
              return (
                <li key={p.key} className={v === 'passed' ? 'is-pass' : v ? 'is-fail' : ''}>
                  <Icon size={16} weight={v ? 'fill' : 'regular'} aria-hidden="true" />
                  <span>{p.title}</span>
                  <em>{v ?? 'not run'}</em>
                </li>
              );
            })}
          </ol>
        </section>

        {/* ---- the studies ---- */}
        <section className="tile" aria-labelledby="studies-h">
          <header className="tile__head">
            <h2 id="studies-h"><Stack size={17} /> Studies</h2>
            <Link to="/studies" className="tile__link">All <ArrowRight size={13} /></Link>
          </header>
          <ul className="study-rows">
            {studies === null && [0, 1, 2].map((i) => <li key={i} className="tile-skeleton tile-skeleton--row" />)}
            {served.slice(0, 4).map((s) => {
              const st = statsFor(s.id);
              return (
                <li key={s.id}>
                  <Link to={`/hologram/${s.id}`} className="study-row">
                    <Thumb study={s} className="study-row__thumb" />
                    <span className="study-row__text">
                      <b>{s.modality ?? 'CT'} {title(s.body_part)}</b>
                      <small>{s.slice_count ?? '?'} slices{st ? `, ${st.total} structures` : ''}</small>
                    </span>
                    {st?.defaceApplied && <span className="chip">face cut</span>}
                    <ArrowRight size={16} className="study-row__go" />
                  </Link>
                </li>
              );
            })}
            <li>
              <Link to="/body" className="study-row">
                <span className="study-row__thumb study-row__thumb--atlas"><PersonArmsSpread size={22} /></span>
                <span className="study-row__text"><b>Full body atlas</b><small>8 systems, steered by hand</small></span>
                <ArrowRight size={16} className="study-row__go" />
              </Link>
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}

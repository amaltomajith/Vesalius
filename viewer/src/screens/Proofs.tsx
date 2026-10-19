import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle, CircleDashed, CircleNotch, EyeSlash, Play, XCircle } from '@phosphor-icons/react';
import { api, type Catalogue, type ProofInfo, type ProofJob } from '../lib/api';
import { Link } from 'react-router';
import { useToast } from '../app/Shell';
import { useCanAct } from '../store/authStore';
import { dataUrl, STATIC } from '../lib/runtime';
import WakeBackend from '../components/WakeBackend';

type Job = ProofJob | { state: 'running' };
type Status = 'pass' | 'fail' | 'running' | 'none';

// the test each proof runs (tests/proofs/), shown so a reader can find it
const FILE: Record<string, string> = {
  privacy: 'test_phi_injection.py',
  face: 'test_defacing_gate.py',
  rebuild: 'test_reproducibility.py',
  contract: 'test_contract_violation.py',
  viewer: 'test_head_study_has_no_face.py',
};

const statusOf = (job: Job | undefined): Status => {
  if (!job) return 'none';
  if (job.state !== 'finished') return 'running';
  return (job as ProofJob).verdict === 'passed' ? 'pass' : 'fail';
};

function StatusPill({ status, verdict }: { status: Status; verdict?: string | null }) {
  const Icon = status === 'pass' ? CheckCircle : status === 'fail' ? XCircle : status === 'running' ? CircleNotch : CircleDashed;
  const word = status === 'pass' ? 'Passed' : status === 'fail' ? (verdict === 'incomplete' ? 'Incomplete' : verdict === 'error' ? 'Error' : 'Failed') : status === 'running' ? 'Running' : 'Not run yet';
  return (
    <span className={`status-pill status-pill--${status}`}>
      <Icon size={15} weight={status === 'pass' || status === 'fail' ? 'fill' : 'regular'} className={status === 'running' ? 'spin' : ''} aria-hidden="true" />
      {word}
    </span>
  );
}

/** The five proofs: automated tests, each trying to break one promise. One job runs at a time. */
export default function Proofs() {
  const { toast } = useToast();
  const [proofs, setProofs] = useState<ProofInfo[] | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [busy, setBusy] = useState(false);
  const canAct = useCanAct();   // a public showcase: the admin runs the proofs, everyone sees the results
  const [imageStamp, setImageStamp] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let live = true;
    void Promise.all([api<Catalogue>('/demo/datasets'), api<Record<string, ProofJob>>('/demo/proofs/last')])
      .then(([info, last]) => {
        if (!live) return;
        setProofs(info.proofs);
        setJobs(last);
        setBusy(info.busy);
      })
      .catch((e: unknown) => toast(e instanceof Error ? e.message : String(e)));
    return () => { live = false; window.clearTimeout(timer.current); };
  }, [toast]);

  const run = useCallback(async (key: string) => {
    try {
      const { job_id } = await api<{ job_id: string }>(`/demo/proofs/${key}`, { method: 'POST' });
      setBusy(true);
      setJobs((j) => ({ ...j, [key]: { state: 'running' } }));
      const tick = async () => {
        try {
          const job = await api<ProofJob>(`/demo/proofs/jobs/${job_id}`);
          setJobs((j) => ({ ...j, [key]: job }));
          if (job.state === 'finished') {
            setBusy(false);
            setImageStamp(Date.now());
            return;
          }
          timer.current = window.setTimeout(() => void tick(), 2000);
        } catch (e) {
          setBusy(false);
          toast(e instanceof Error ? e.message : String(e));
        }
      };
      void tick();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e));
    }
  }, [toast]);

  const statuses = (proofs ?? []).map((p) => statusOf(jobs[p.key]));
  const count = (s: Status) => statuses.filter((x) => x === s).length;

  return (
    <div className="page page--dash">
      <section className="tile tile--wide page-tile" aria-labelledby="proofs-h">
        <div className="page-tile__copy">
          <h1 id="proofs-h">Proofs</h1>
          <p className="big-num">{proofs ? `${count('pass')} / ${proofs.length}` : '0'}<span>{STATIC ? 'passing in the last recorded run' : 'passing on this machine'}</span></p>
          <p className="tile-note">Each proof is an automated test that tries to break one promise. One runs at a time.</p>
          {STATIC && <WakeBackend target="proofs" what="Running a proof yourself" />}
        </div>
        {proofs && (
          <dl className="facts facts--tally">
            <div className="is-pass"><dt>Passed</dt><dd>{count('pass')}</dd></div>
            <div className="is-fail"><dt>Failed</dt><dd>{count('fail')}</dd></div>
            <div><dt>Not run</dt><dd>{count('none') + count('running')}</dd></div>
          </dl>
        )}
      </section>

      <ol className="proof-grid" data-testid="proof-list">
        {!proofs && [0, 1, 2, 3, 4].map((i) => <li key={i} className="tile tile-skeleton tile-skeleton--proof" />)}
        {proofs?.map((p) => {
          const job = jobs[p.key];
          const status = statusOf(job);
          const finished = job && job.state === 'finished' ? (job as ProofJob) : null;
          return (
            <li key={p.key} className={`tile proof-card proof-card--${p.key}`} data-proof={p.key}>
              <div className="proof-card__top">
                <span className="mono proof-card__num">{String(p.number).padStart(2, '0')}</span>
                <StatusPill status={status} verdict={finished?.verdict} />
              </div>
              <h2>{p.title}</h2>
              <p className="proof-card__claim">{p.claim}</p>
              <p className="proof-card__how">{p.plain}</p>

              {p.key === 'face' && (
                finished?.verdict === 'passed' ? (
                  <figure className="proof-card__figure">
                    <img src={STATIC ? dataUrl('proofs/defacing.png') : `/demo/proofs/image?t=${imageStamp}`} alt="The same head with and without face removal, side by side" />
                    <figcaption>
                      Left: without face removal, which the pipeline refuses to produce. Right: what it produces. The
                      red circle marks where the nose was.
                    </figcaption>
                  </figure>
                ) : (
                  <div className="proof-card__slot">
                    <EyeSlash size={22} />
                    <span>Run it to see the same head with and without face removal, side by side.</span>
                  </div>
                )
              )}

              {status === 'running' && <div className="run-bar" aria-hidden="true" />}
              {finished && (
                <div className="proof-card__result">
                  <span><b>{finished.passed}</b> passed</span>
                  <span><b>{finished.failed}</b> failed</span>
                  {finished.skipped > 0 && <span><b>{finished.skipped}</b> skipped</span>}
                  {finished.summary && <p>{finished.summary}</p>}
                </div>
              )}
              {finished && finished.verdict !== 'passed' && finished.detail && (
                <details className="proof-card__detail">
                  <summary>Show the test output</summary>
                  <pre>{finished.detail}</pre>
                </details>
              )}

              <div className="proof-card__foot">
                <span className="mono" title={`tests/proofs/${FILE[p.key] ?? ''}`}>tests/proofs/{FILE[p.key] ?? ''}</span>
                {STATIC ? null : canAct ? (
                  <button type="button" className="btn btn-dark btn-sm" onClick={() => void run(p.key)} disabled={busy}>
                    <Play size={13} weight="fill" /> Run, about {p.approx_seconds} s
                  </button>
                ) : (
                  <Link className="btn btn-sm" to="/login?next=/proofs" data-testid="proof-sign-in">Admin runs this</Link>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

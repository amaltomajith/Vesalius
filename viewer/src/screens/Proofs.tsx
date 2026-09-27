import { useCallback, useEffect, useRef, useState } from 'react';
import { Play } from '@phosphor-icons/react';
import { api, type Catalogue, type ProofInfo, type ProofJob } from '../lib/api';
import { Link } from 'react-router';
import { useToast } from '../app/Shell';
import { useCanAct } from '../store/authStore';

const VERDICT: Record<string, [string, string]> = {
  passed: ['pass', 'passed'],
  failed: ['reject', 'failed'],
  error: ['reject', 'error'],
  incomplete: ['', 'incomplete'],
};

/** The five proofs: automated tests, each trying to break one promise. One job runs at a time. */
export default function Proofs() {
  const { toast } = useToast();
  const [proofs, setProofs] = useState<ProofInfo[] | null>(null);
  const [jobs, setJobs] = useState<Record<string, ProofJob | { state: 'running' }>>({});
  const [busy, setBusy] = useState(false);
  const [imageStamp, setImageStamp] = useState(0);
  const timer = useRef<number | undefined>(undefined);
  const canAct = useCanAct();

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

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="kicker mono">Proofs</p>
          <h1>Claims are cheap. These are tests.</h1>
          <p className="lede">Each proof is an automated test that tries to break one promise. One job runs at a time.</p>
        </div>
      </header>

      <ol className="proof-list" data-testid="proof-list">
        {!proofs && [0, 1, 2, 3, 4].map((i) => <li key={i} className="card skeleton" />)}
        {proofs?.map((p) => {
          const job = jobs[p.key];
          const running = job?.state === 'running' || (job && job.state !== 'finished');
          const finished = job && job.state === 'finished' ? (job as ProofJob) : null;
          const [cls, label] = finished ? VERDICT[finished.verdict ?? 'error'] ?? ['reject', 'error'] : running ? ['live', 'running'] : ['', 'not run yet'];
          return (
            <li key={p.key} className="card proof" data-proof={p.key}>
              <span className="proof__num mono">{String(p.number).padStart(2, '0')}</span>
              <div className="proof__body">
                <h2>{p.title} <span className={`chip ${cls}`}>{label}</span></h2>
                <p>{p.plain}</p>
                {finished?.summary && <p className="proof__summary">{finished.summary}</p>}
                {finished && finished.verdict !== 'passed' && finished.detail && <pre className="proof__detail">{finished.detail}</pre>}
                {p.key === 'face' && finished?.verdict === 'passed' && (
                  <figure className="proof__figure">
                    <img src={`/demo/proofs/image?t=${imageStamp}`} alt="The same head with and without face removal, side by side" />
                    <figcaption>
                      Left: without face removal, which the pipeline refuses to produce. Right: what it
                      produces. The red circle marks where the nose was.
                    </figcaption>
                  </figure>
                )}
              </div>
              {canAct ? (
                <button className="btn btn-sm" onClick={() => void run(p.key)} disabled={busy}>
                  <Play size={14} weight="fill" /> Run · {p.approx_seconds} s
                </button>
              ) : (
                <Link className="btn btn-sm" to="/login?next=/proofs">Sign in to run</Link>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

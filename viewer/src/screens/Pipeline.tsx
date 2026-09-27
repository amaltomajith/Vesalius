import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import {
  ArrowCounterClockwise, Check, CheckCircle, Clock, Cube, Database, DownloadSimple, FileText,
  FileZip, Play, UploadSimple, WarningOctagon, CornersOut,
} from '@phosphor-icons/react';
import {
  api, mb, STAGE_NAMES, STEP_CODE,
  type Artifact, type Catalogue, type Dataset, type LineageNode, type Quality, type Run, type Series,
} from '../lib/api';
import { useToast } from '../app/Shell';
import { useCanAct } from '../store/authStore';
import PreviewCanvas from '../components/PreviewCanvas';

type Panel = 'idle' | 'upload' | 'run' | 'result' | 'rejected' | 'error';
type Body = 'auto' | 'head' | 'chest' | 'abdomen';
type Preset = 'bone' | 'skin_surface' | 'structures';

interface Result {
  run: Run;
  series: Series;
  sceneId: string; // for /hologram/:id
  previewUrl: string;
  modelUrl: string;
  thumbUrl: string | null;
  lineage: LineageNode[];
  quality: Quality;
}

const secs = (s: number | null) => (s == null ? '' : `${s.toFixed(1)} s`);

/** Run a scan: the console, ported. Only the fields that matter right now are on screen. */
export default function Pipeline() {
  const { toast } = useToast();
  const navigate = useNavigate();
  const [info, setInfo] = useState<Catalogue | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>('idle');
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [errorText, setErrorText] = useState('');
  const pollRef = useRef<number | undefined>(undefined);

  // upload form
  const [file, setFile] = useState<File | null>(null);
  const [ack, setAck] = useState(false);
  const [body, setBody] = useState<Body>('auto');
  const [preset, setPreset] = useState<Preset>('bone');
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);

  const showError = (message: string) => {
    setErrorText(message || 'The pipeline stopped without a reason.');
    setPanel('error');
  };

  const showResult = useCallback(async (r: Run) => {
    const sid = r.series_ids[0];
    const [series, arts, quality, lineage] = await Promise.all([
      api<Series>(`/series/${sid}`),
      api<{ artifacts: Artifact[] }>(`/series/${sid}/artifacts`),
      api<Quality>(`/series/${sid}/quality`),
      api<{ nodes: LineageNode[] }>(`/series/${sid}/lineage`),
    ]);
    const byKind = Object.fromEntries(arts.artifacts.map((a) => [a.kind, a]));
    const structures = r.preset === 'structures';
    const groups = arts.artifacts.filter((a) => a.kind === 'mesh_group_glb');
    const glb = structures ? groups.reduce((big, a) => (a.bytes > big.bytes ? a : big), groups[0]) : byKind.surface_glb;
    if (!glb) throw new Error('The run finished but its 3D file is not in gold.');
    const seen = new Set<string>();
    const ordered = lineage.nodes
      .slice()
      .sort((a, b) => Number(a.stage.slice(1)) - Number(b.stage.slice(1)))
      .filter((n) => (seen.has(n.stage) ? false : (seen.add(n.stage), true)));
    setResult({
      run: r,
      series,
      sceneId: structures ? sid : `surface:${sid}`,
      previewUrl: `/demo/files/${glb.id}`,
      modelUrl: `/demo/files/${glb.id}?download=1`,
      thumbUrl: byKind.thumbnail ? `/demo/files/${byKind.thumbnail.id}` : null,
      lineage: ordered,
      quality,
    });
    setPanel('result');
  }, []);

  const finish = useCallback(async (r: Run) => {
    if (r.outcome === 'rejected') { setPanel('rejected'); return; }
    if (r.outcome !== 'success') { showError(r.message); return; }
    try { await showResult(r); } catch (e) { showError(e instanceof Error ? e.message : String(e)); }
  }, [showResult]);

  const watch = useCallback((runId: string) => {
    setPanel('run');
    setBusy(true);
    setResult(null);
    window.clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await api<Run>(`/demo/runs/${runId}`);
        setRun(r);
        if (r.state === 'finished') {
          window.clearInterval(pollRef.current);
          setBusy(false);
          await finish(r);
        }
      } catch (e) {
        window.clearInterval(pollRef.current);
        setBusy(false);
        showError(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    pollRef.current = window.setInterval(() => void tick(), 1000);
  }, [finish]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const c = await api<Catalogue>('/demo/datasets');
        if (!live) return;
        setInfo(c);
        setSelected(c.datasets[0]?.key ?? null);
        if (c.busy && c.latest_run) {
          // resume following a scan only if the latest run is the thing still running
          const r = await api<Run>(`/demo/runs/${c.latest_run}`).catch(() => null);
          if (live && r && r.state === 'running') watch(r.id);
          else if (live) { setBusy(true); toast('A proof is running. Scans can start once it finishes.'); }
        }
      } catch (e) { toast(e instanceof Error ? e.message : String(e)); }
    })();
    return () => { live = false; window.clearInterval(pollRef.current); };
  }, [toast, watch]);

  const choose = (key: string) => {
    if (busy) return;
    setSelected(key);
    setPanel(key === 'upload' ? 'upload' : 'idle');
  };

  const startSample = async () => {
    if (!selected || selected === 'upload') return;
    try {
      const { run_id } = await api<{ run_id: string }>('/demo/runs/sample', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset: selected }),
      });
      watch(run_id);
    } catch (e) { toast(e instanceof Error ? e.message : String(e)); }
  };

  const maxBytes = (info?.upload.max_megabytes ?? 0) * 1048576;
  const faceMatters = body === 'auto' || body === 'head';
  useEffect(() => { if (!faceMatters && preset === 'skin_surface') setPreset('bone'); }, [faceMatters, preset]);
  let why = '';
  if (!file) why = 'Choose a file to begin.';
  else if (!file.name.toLowerCase().endsWith('.zip')) why = 'That is not a .zip file.';
  else if (maxBytes && file.size > maxBytes) why = `That file is over the ${mb(maxBytes)} limit.`;
  else if (!ack) why = 'Confirm the data is public or anonymous.';
  else if (busy) why = 'Another job is running.';
  if (uploading) why = 'Uploading…';

  const submitUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (why || !file) return;
    const form = new FormData();
    form.append('file', file);
    form.append('acknowledged', 'true');
    form.append('preset', preset);
    form.append('body_part', body);
    setUploading(true);
    try {
      const { run_id } = await api<{ run_id: string }>('/demo/runs/upload', { method: 'POST', body: form });
      watch(run_id);
    } catch (err) { toast(err instanceof Error ? err.message : String(err)); }
    finally { setUploading(false); }
  };

  const reset = () => {
    if (busy) return;
    setRun(null);
    setResult(null);
    setPanel(selected === 'upload' ? 'upload' : 'idle');
  };

  const canAct = useCanAct();
  const dataset: Dataset | undefined = info?.datasets.find((d) => d.key === selected);
  if (!canAct) {
    return (
      <div className="page">
        <header className="page-head">
          <div>
            <p className="kicker mono">Run a scan</p>
            <h1>Running the pipeline is for the admin</h1>
            <p className="lede">
              This is a public showcase: uploads and runs are switched off so that no one can send it a
              real patient scan. Everything the pipeline has already published is open to explore.
            </p>
          </div>
        </header>
        <div className="chip-row">
          <Link className="btn btn-primary" to="/studies">Explore the studies</Link>
          <Link className="btn" to="/body">The full body</Link>
          <Link className="btn" to="/login?next=/pipeline" data-testid="gate-sign-in">Admin sign in</Link>
        </div>
      </div>
    );
  }
  const locked = panel === 'run' || busy;

  return (
    <div className="page page--pipeline">
      <header className="page-head">
        <div>
          <p className="kicker mono">Run a scan</p>
          <h1>From DICOM to a governed 3D model</h1>
        </div>
        {(panel === 'result' || panel === 'rejected' || panel === 'error') && (
          <button className="btn btn-sm" onClick={reset} disabled={busy}>
            <ArrowCounterClockwise size={15} /> New run
          </button>
        )}
      </header>

      <div className="run-layout">
        <aside className={`rail card${locked ? ' rail--locked' : ''}`} aria-label="Choose a scan">
          <h2 className="rail__title">Scans</h2>
          <div role="radiogroup" aria-label="Sample scans" className="scan-list">
            {!info && [0, 1, 2].map((i) => <div key={i} className="skeleton scan-skeleton" />)}
            {info?.datasets.map((d) => (
              <button key={d.key} className="scan" role="radio" aria-checked={selected === d.key}
                onClick={() => choose(d.key)} disabled={locked} data-key={d.key}>
                {d.expected === 'rejected' ? <WarningOctagon size={18} /> : <Cube size={18} />}
                <span><b>{d.title}</b><small>{d.blurb}</small></span>
              </button>
            ))}
          </div>
          <button className="scan scan--upload" role="radio" aria-checked={selected === 'upload'}
            onClick={() => choose('upload')} disabled={locked}>
            <UploadSimple size={18} />
            <span><b>Upload your own</b><small>A .zip of one DICOM series</small></span>
          </button>
        </aside>

        <section className="stage card" aria-live="polite">
          {panel === 'idle' && (
            <div className="panel" data-testid="panel-idle">
              <p className="kicker mono">{dataset ? (dataset.synthetic ? 'Synthetic scan' : 'Public test file') : ''}</p>
              <h2 className="panel__title">{dataset?.title ?? 'Choose a scan'}</h2>
              <p className="lede">{dataset?.shows}</p>
              {dataset && (
                <div className="chip-row">
                  <span className="chip"><Clock size={13} /> About {dataset.approx_seconds} s</span>
                  {dataset.expected === 'rejected' && <span className="chip reject">Rejected by design</span>}
                </div>
              )}
              <button className="btn btn-primary" onClick={() => void startSample()} disabled={busy || !dataset} data-testid="run-sample">
                <Play size={15} weight="fill" /> Run this scan
              </button>
            </div>
          )}

          {panel === 'upload' && (
            <form className="panel" onSubmit={(e) => void submitUpload(e)} noValidate data-testid="panel-upload">
              <h2 className="panel__title">Upload a scan</h2>
              <p className="lede">Only public or anonymous data. The original is kept exactly as received in restricted storage.</p>
              <label className={`drop${dragOver ? ' drop--over' : ''}${file ? ' drop--has-file' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }}>
                <input type="file" accept=".zip" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                <FileZip size={26} />
                <span>{file ? <><b>{file.name}</b> {mb(file.size)}, click to change</> : <><b>Drop a .zip here</b> or browse</>}</span>
                {info && <small>One series, up to {info.upload.max_megabytes} MB</small>}
              </label>

              {file && (
                <label className="field check">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                  <span>I confirm this is public or anonymous data, not a real patient&apos;s scan.</span>
                </label>
              )}

              {ack && (
                <fieldset className="field">
                  <legend>What does the scan show?</legend>
                  <div className="seg">
                    {(['auto', 'head', 'chest', 'abdomen'] as Body[]).map((b) => (
                      <label key={b}><input type="radio" name="body" checked={body === b} onChange={() => setBody(b)} />
                        <span>{b === 'auto' ? 'Let the files say' : b.charAt(0).toUpperCase() + b.slice(1)}</span></label>
                    ))}
                  </div>
                  {body === 'auto' && <p className="help">Unlabelled scans are refused for safety. A label inside the files always wins, and a scan that names a head is never treated as anything else.</p>}
                </fieldset>
              )}

              {ack && (
                <fieldset className="field">
                  <legend>Make a 3D model of</legend>
                  <div className="seg">
                    <label><input type="radio" name="preset" checked={preset === 'bone'} onChange={() => setPreset('bone')} /><span>Bone</span></label>
                    {faceMatters && <label><input type="radio" name="preset" checked={preset === 'skin_surface'} onChange={() => setPreset('skin_surface')} /><span>Skin surface</span></label>}
                    <label><input type="radio" name="preset" checked={preset === 'structures'} onChange={() => setPreset('structures')} /><span>Named structures</span></label>
                  </div>
                  {preset === 'structures'
                    ? <p className="help">Every organ, bone and vessel is found and named by TotalSegmentator on this machine&apos;s CPU. Takes a few minutes.</p>
                    : faceMatters && <p className="help">For a head, the face is removed before any surface is made.</p>}
                </fieldset>
              )}

              <div className="form-actions">
                <button className="btn btn-primary" type="submit" disabled={!!why}>
                  <Play size={15} weight="fill" /> Run my scan
                </button>
                <p className="help">{why}</p>
              </div>
            </form>
          )}

          {panel === 'run' && run && <RunView run={run} />}

          {panel === 'result' && result && (
            <div className="panel panel--wide" data-testid="panel-result">
              <div className="result-head">
                <div>
                  <p className="kicker mono">Published to gold</p>
                  <h2 className="panel__title">{result.run.title}</h2>
                </div>
                <div className="chip-row">
                  <a className="btn btn-sm" href={result.modelUrl}><DownloadSimple size={15} /> Model</a>
                  <a className="btn btn-sm" href={`/demo/report/${result.run.id}`} target="_blank" rel="noopener"><FileText size={15} /> Report</a>
                  <button className="btn btn-sm btn-primary" onClick={() => navigate(`/hologram/${result.sceneId}`)} data-testid="enter-hologram">
                    <CornersOut size={15} /> Enter hologram
                  </button>
                </div>
              </div>
              <div className="result-stage">
                <PreviewCanvas url={result.previewUrl} />
                {result.thumbUrl && <img className="result-thumb" src={result.thumbUrl} alt="One slice through the de-identified volume" />}
              </div>
              <dl className="fact-strip">
                <Fact k="Images" v={String(result.series.instance_count)} />
                <Fact k="Body part" v={String(result.series.body_part ?? '·').toLowerCase()} />
                <Fact k="Face" v={result.series.deface_required ? 'Removed, verified' : 'Not required'} ok={result.series.deface_required} />
                <Fact k="Quality" v={result.series.quality_status === 'pass' ? 'Passed' : result.series.quality_status} ok={result.series.quality_status === 'pass'} />
              </dl>
              {result.run.planted.length > 0 && (
                <details className="disclose">
                  <summary>{result.run.planted.length} planted identifier{result.run.planted.length === 1 ? '' : 's'} removed</summary>
                  <ul className="kv">{result.run.planted.map((p) => <li key={p.label}><b>{p.label}</b><s>{p.value}</s></li>)}</ul>
                </details>
              )}
              <details className="disclose">
                <summary>Audit trail</summary>
                <div className="audit">
                  <div>
                    <h3>Lineage</h3>
                    <ol className="audit-list">
                      {result.lineage.map((n) => <li key={n.stage}><span className="mono">{n.stage}</span><span>{STAGE_NAMES[n.stage] ?? n.stage}</span><span className="mono">v{n.stage_version}</span></li>)}
                    </ol>
                  </div>
                  <div>
                    <h3>Stage runs</h3>
                    <ol className="audit-list">
                      {result.quality.stages.map((s) => <li key={s.stage}><span className="mono">{s.stage}</span><span>{`${STAGE_NAMES[s.stage] ?? ''} ${s.latest_status}`.trim()}</span><span className="mono">{s.run_count}×</span></li>)}
                      {!result.quality.quarantine.length && <li className="span">Nothing quarantined.</li>}
                    </ol>
                  </div>
                </div>
                <p className="help">Data contract <span className="mono">{result.series.contract_version}</span></p>
              </details>
            </div>
          )}

          {panel === 'rejected' && run && (() => {
            const at = run.steps.find((s) => s.state === 'rejected');
            return (
              <div className="panel" data-testid="panel-rejected">
                <p className="kicker mono">{at ? `Stopped at ${STEP_CODE[at.key]} · ${at.title}` : 'Stopped'}</p>
                <h2 className="panel__title">Rejected, as designed</h2>
                <p className="lede">{run.message}</p>
                <div className="note">
                  <Database size={18} />
                  <p>The series is kept in quarantine with a reason code. Nothing downstream ran on it, and nothing was dropped.</p>
                </div>
              </div>
            );
          })()}

          {panel === 'error' && (
            <div className="panel" data-testid="panel-error">
              <p className="kicker mono">Something went wrong</p>
              <h2 className="panel__title">The run did not finish</h2>
              <p className="lede">{errorText}</p>
              <button className="btn" onClick={reset}>Back to scans</button>
            </div>
          )}
        </section>
      </div>
      <p className="page-foot">
        Results appear in <Link to="/studies">Studies</Link> when they are published in named-structure mode.
      </p>
    </div>
  );
}

function Fact({ k, v, ok = false }: { k: string; v: string; ok?: boolean }) {
  return (
    <div>
      <dt>{k}</dt>
      <dd>{ok && <CheckCircle size={15} weight="fill" />} {v}</dd>
    </div>
  );
}

function RunView({ run }: { run: Run }) {
  const [, setNow] = useState(0);
  useEffect(() => {
    if (run.state === 'finished') return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [run.state]);
  const end = run.finished_at ?? Date.now() / 1000;
  const active = run.steps.find((s) => s.state === 'running') ?? [...run.steps].reverse().find((s) => s.state !== 'pending');
  const done = run.steps.filter((s) => s.state === 'done' && s !== active);
  return (
    <div className="panel" data-testid="panel-run">
      <div className="run-head">
        <h2 className="panel__title">{run.title}</h2>
        <span className="mono run-clock">{Math.max(0, end - run.started_at).toFixed(0)} s</span>
      </div>
      <ol className="steps" aria-label="Pipeline progress">
        {run.steps.map((s) => (
          <li key={s.key} className={s.state} title={`${s.title}: ${s.state}`}>
            <span className="bar-seg" />
            <span className="code">{STEP_CODE[s.key] ?? ''}</span>
          </li>
        ))}
      </ol>
      {active && (
        <div className="current">
          <div className="top">
            <h3><span className="mono kicker">{STEP_CODE[active.key] ?? ''}&nbsp;&nbsp;</span>{active.title}</h3>
            <span className="mono run-clock">{secs(active.seconds)}</span>
          </div>
          <p>{active.detail || active.what}</p>
          {done.length > 0 && (
            <div className="done-list">
              {done.map((s) => <span key={s.key}><Check size={13} /> {s.title} {secs(s.seconds)}</span>)}
            </div>
          )}
        </div>
      )}
      {run.planted.length > 0 && run.state !== 'finished' && (
        <details className="disclose">
          <summary>Fake identifiers planted in this scan</summary>
          <ul className="kv">{run.planted.map((p) => <li key={p.label}><b>{p.label}</b><s>{p.value}</s></li>)}</ul>
        </details>
      )}
    </div>
  );
}

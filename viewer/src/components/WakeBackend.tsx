import { useEffect, useRef, useState } from 'react';
import { CheckCircle, CircleDashed, CircleNotch, Power } from '@phosphor-icons/react';
import { loadConfig, type SiteConfig } from '../lib/runtime';
import { backendTarget, getStatus, phaseOf, requestWake, WakeRefused, type Phase } from '../lib/wake';

const POLL_MS = 4000;
const GIVE_UP_MS = 8 * 60 * 1000;
const STEPS: { phase: Phase; label: string }[] = [
  { phase: 'starting', label: 'Starting the server' },
  { phase: 'services', label: 'Starting the pipeline, database and storage' },
  { phase: 'ready', label: 'Ready' },
];
const ORDER: Phase[] = ['asleep', 'stopping', 'starting', 'services', 'ready'];

type View = { kind: 'idle' } | { kind: 'waking'; phase: Phase; since: number } | { kind: 'ready'; href: string } | { kind: 'error'; message: string };

/**
 * The static site's door to the live backend (runs, proofs, the AI). It is asleep to save its
 * credit: this wakes it (lib/wake.ts), waits for it to be ready and opens the same screen there.
 * `target` is the app path to open on the backend ("pipeline", "proofs", "body", ...).
 */
export default function WakeBackend({ target, what, compact = false }: { target: string; what: string; compact?: boolean }) {
  const [cfg, setCfg] = useState<SiteConfig | null>(null);
  const [view, setView] = useState<View>({ kind: 'idle' });
  const timer = useRef<number | undefined>(undefined);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    void loadConfig().then((c) => { if (live.current) setCfg(c); });
    return () => { live.current = false; window.clearTimeout(timer.current); };
  }, []);

  if (!cfg) return null;
  if (!cfg.wakeUrl) {
    return <p className={compact ? 'ai-off' : 'tile-note'} data-testid="wake-none">{what} runs on the live pipeline, which this copy of the site does not offer.</p>;
  }

  const wake = async () => {
    const started = Date.now();
    let rewoken = false;
    try {
      let status = await requestWake(cfg);
      for (;;) {
        if (!live.current) return;
        const phase = phaseOf(status);
        if (phase === 'ready') {
          const href = backendTarget(cfg, target);
          if (!href) throw new WakeRefused('The live backend is awake, but this site does not know its address.');
          setView({ kind: 'ready', href });
          window.location.assign(href);
          return;
        }
        if (phase === 'unavailable') throw new WakeRefused(status.message ?? 'The live backend did not start.');
        if (Date.now() - started > GIVE_UP_MS) throw new WakeRefused('The live backend is taking too long to start. Try again in a few minutes.');
        // it was just going to sleep when we asked: once it is down, ask once more
        if (phase === 'asleep' && Date.now() - started > POLL_MS * 3 && !rewoken) {
          rewoken = true;
          status = await requestWake(cfg);
          continue;
        }
        setView({ kind: 'waking', phase, since: started });
        await new Promise<void>((resolve) => { timer.current = window.setTimeout(resolve, POLL_MS); });
        status = await getStatus(cfg);
      }
    } catch (e) {
      if (live.current) setView({ kind: 'error', message: e instanceof Error ? e.message : String(e) });
    }
  };

  const reached = (p: Phase) => view.kind === 'ready' || (view.kind === 'waking' && ORDER.indexOf(view.phase) >= ORDER.indexOf(p));

  return (
    <div className={compact ? 'wake wake--compact' : 'wake'} data-testid="wake">
      {view.kind === 'idle' && (
        <>
          <p className="wake__lede">
            {what} runs on the live pipeline. It sleeps when nobody is using it; waking it takes about two to three
            minutes, and it switches itself off again when you are done.
          </p>
          <button type="button" className="btn btn-dark" onClick={() => { setView({ kind: 'waking', phase: 'starting', since: Date.now() }); void wake(); }} data-testid="wake-button">
            <Power size={15} weight="bold" /> Wake the live pipeline
          </button>
        </>
      )}
      {(view.kind === 'waking' || view.kind === 'ready') && (
        <ol className="wake__steps" aria-live="polite">
          {STEPS.map((s) => {
            const done = reached(s.phase) && !(view.kind === 'waking' && view.phase === s.phase);
            const now = view.kind === 'waking' && view.phase === s.phase;
            const Icon = done ? CheckCircle : now ? CircleNotch : CircleDashed;
            return (
              <li key={s.phase} className={done ? 'is-done' : now ? 'is-now' : ''}>
                <Icon size={16} weight={done ? 'fill' : 'regular'} className={now ? 'spin' : ''} aria-hidden="true" /> {s.label}
              </li>
            );
          })}
          {view.kind === 'ready' && <li><a href={view.href}>Open it</a> if it did not open by itself.</li>}
        </ol>
      )}
      {view.kind === 'error' && (
        <p className="wake__error" role="alert" data-testid="wake-error">
          {view.message}{' '}
          <button type="button" className="btn btn-sm" onClick={() => setView({ kind: 'idle' })}>Back</button>
        </p>
      )}
    </div>
  );
}

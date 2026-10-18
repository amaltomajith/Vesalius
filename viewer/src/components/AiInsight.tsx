import { useEffect, useRef, useState } from 'react';
import { Sparkle } from '@phosphor-icons/react';
import { aiStatus, streamAtlasInsight, streamStudyInsight, type AiStatus } from '../services/ai';

type Props =
  | { kind: 'study'; node: string; label: string; studyId: string; snapshot: null }
  | { kind: 'atlas'; node: string; label: string; snapshot: string | null; studyId?: undefined };

/**
 * Optional AI explanation (off unless the server has a key; the key never reaches the browser).
 * Scans: the SERVER builds the context from the pipeline's measurements; this component sends
 * only {study_id, node} and never an image (CLAUDE.md hard rule 8). Atlas: may include the
 * framebuffer snapshot, because the atlas is not a patient.
 */
export default function AiInsight(props: Props) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // request sequence guard (VIEWER-CONSTRAINTS activeRequestRef): a stale stream never writes
  const activeRequestRef = useRef(0);

  useEffect(() => {
    let live = true;
    void aiStatus().then((s) => { if (live) setStatus(s); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    activeRequestRef.current++;
    setText('');
    setBusy(false);
    setError(null);
  }, [props.node]);

  if (!status) return null;
  if (!status.enabled) {
    return (
      <p className="ai-off" data-testid="ai-off">
        AI explanation is off. It needs a GROQ_API_KEY in the server's .env.
      </p>
    );
  }

  const run = async () => {
    const id = ++activeRequestRef.current;
    setText('');
    setError(null);
    setBusy(true);
    try {
      const stream =
        props.kind === 'study'
          ? streamStudyInsight({ study_id: props.studyId, node: props.node })
          : streamAtlasInsight({ node: props.node, label: props.label, image: props.snapshot });
      for await (const chunk of stream) {
        if (id !== activeRequestRef.current) return;
        setText((t) => t + chunk);
      }
    } catch (err) {
      if (id === activeRequestRef.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (id === activeRequestRef.current) setBusy(false);
    }
  };

  return (
    <div className="ai-insight-box ai-insight-box--ai" data-testid="ai-insight">
      <div className="ai-insight-box__header">
        <span className="ai-insight-box__title">
          <Sparkle size={12} weight="fill" /> AI explanation
        </span>
        <span className="ai-insight-box__status">
          {busy ? 'Writing' : props.kind === 'study' ? 'From measurements' : props.snapshot ? 'From your snapshot' : 'From the name'}
        </span>
      </div>
      {text && <div className="ai-insight-box__content">{text}</div>}
      {error && <div className="ai-insight-box__content ai-error">{error}</div>}
      {!text && !busy && (
        <button className="ai-run hud-interactive" onClick={() => void run()}>
          Explain {props.kind === 'study' ? 'these measurements' : props.label}
        </button>
      )}
      {props.kind === 'atlas' && !props.snapshot && !text && (
        <p className="ai-hint">Point both index fingers at each other to add a snapshot of the view.</p>
      )}
      <div className="context-disclaimer">Visualisation support, not diagnosis.</div>
    </div>
  );
}

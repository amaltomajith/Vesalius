// Client for the optional AI endpoints (src/api/routers/ai.py). The app only ever talks to its
// own server; the server holds the key and calls the model. Studies send an id and a node, never
// pixels (CLAUDE.md hard rule 8); only the atlas may send a snapshot.

import { STATIC } from '../lib/runtime';

export interface AiStatus {
  enabled: boolean;
  vision: boolean;
}

let statusPromise: Promise<AiStatus> | null = null;

export function aiStatus(): Promise<AiStatus> {
  if (STATIC) return Promise.resolve({ enabled: false, vision: false }); // no server on the static site
  if (!statusPromise) {
    statusPromise = fetch('/ai/status')
      .then((r) => (r.ok ? (r.json() as Promise<AiStatus>) : { enabled: false, vision: false }))
      .catch(() => ({ enabled: false, vision: false }));
  }
  return statusPromise;
}

export interface StudyInsightRequest {
  study_id: string;
  node: string;
}

export interface AtlasInsightRequest {
  node: string;
  label: string;
  image: string | null; // a data: URL of the atlas view, or null
}

async function* stream(path: string, body: StudyInsightRequest | AtlasInsightRequest): AsyncGenerator<string> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok || !r.body) {
    let detail = `${r.status}`;
    try { detail = ((await r.json()) as { detail?: string }).detail ?? detail; } catch { /* not json */ }
    throw new Error(`AI explanation unavailable: ${detail}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      yield decoder.decode(value, { stream: true });
    }
  } finally {
    reader.releaseLock();
  }
}

export const streamStudyInsight = (req: StudyInsightRequest) => stream('/ai/explain/study', req);
export const streamAtlasInsight = (req: AtlasInsightRequest) => stream('/ai/explain/atlas', req);

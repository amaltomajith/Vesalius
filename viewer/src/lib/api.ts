// The app's view of the pipeline's HTTP API (all same-origin: /demo/*, /series/*, /studies).
// Server text is rendered as React text, never as markup. On the static site (lib/runtime.ts) the
// reads it knows come from exported files and everything else throws BackendAsleep.
import { BackendAsleep, readUrl, STATIC } from './runtime';

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (STATIC && (options.method ?? 'GET').toUpperCase() !== 'GET') throw new BackendAsleep();
  const url = readUrl(path);
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error('Cannot reach the Vesalius server. Is it still running?');
  }
  let body: unknown = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const detail = body && typeof (body as { detail?: unknown }).detail === 'string'
      ? (body as { detail: string }).detail
      : `Something went wrong (${response.status}).`;
    throw new Error(detail);
  }
  return body as T;
}

export interface Dataset {
  key: string;
  title: string;
  blurb: string;
  shows: string;
  approx_seconds: number;
  synthetic: boolean;
  expected: 'success' | 'rejected';
}

export interface ProofInfo {
  key: string;
  number: number;
  title: string;
  claim: string;
  plain: string;
  approx_seconds: number;
}

export interface Catalogue {
  datasets: Dataset[];
  proofs: ProofInfo[];
  upload: { max_megabytes: number; presets?: string[] };
  busy: boolean;
  latest_run: string | null;
}

export interface Step {
  key: string;
  title: string;
  what: string;
  state: 'pending' | 'running' | 'done' | 'rejected' | 'failed' | 'skipped';
  detail: string;
  seconds: number | null;
}

export interface Planted { label: string; value: string }

export interface Run {
  id: string;
  dataset: string;
  title: string;
  preset: string;
  state: 'running' | 'finished';
  outcome: 'success' | 'rejected' | 'error' | null;
  message: string;
  steps: Step[];
  series_ids: string[];
  planted: Planted[];
  started_at: number;
  finished_at: number | null;
}

export interface ProofJob {
  id: string;
  key: string;
  state: 'running' | 'finished';
  verdict: 'passed' | 'failed' | 'incomplete' | 'error' | null;
  passed: number;
  failed: number;
  skipped: number;
  summary: string;
  detail: string;
}

export interface Series {
  id: string;
  instance_count: number;
  body_part: string | null;
  quality_status: string;
  deface_required: boolean;
  contract_version: string;
}

export interface Artifact { id: string; kind: string; bytes: number }
export interface LineageNode { stage: string; stage_version: string }
export interface Quality {
  stages: { stage: string; latest_status: string; run_count: number }[];
  quarantine: unknown[];
}

/** the page steps and the pipeline stages they cover (privacy is S4 + S5) */
export const STEP_CODE: Record<string, string> = {
  receive: 'S1', catalog: 'S2', quality: 'S3', privacy: 'S4-5', volume: 'S6',
  face: 'S7', segment: 'S8', surface: 'S9', smooth: 'S10', package: 'S11',
};
export const STAGE_NAMES: Record<string, string> = {
  S1: 'Ingest', S2: 'Index', S3: 'Quality gate', S4: 'De-identify', S5: 'Verify', S6: 'Volume',
  S7: 'Deface', S8: 'Segment', S9: 'Surface', S10: 'Mesh', S11: 'Export', S12: 'Serve',
};

export const mb = (bytes: number) =>
  bytes < 1048576
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0)} MB`;

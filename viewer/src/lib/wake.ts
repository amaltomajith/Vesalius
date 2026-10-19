// The static site's only call to anything other than its own files: the wake function
// (deploy/azure/wake) that starts the live backend's VM on demand. Its URL comes from the site's
// config.json, never from this source. Nothing about the visitor or any study is sent: the wake
// request has no body, the status request has no parameters.

import type { SiteConfig } from './runtime';

export interface WakeStatus {
  power: string; // the VM's power state: deallocated | starting | running | deallocating | ...
  ready: boolean; // the backend answered /health/ready (database and object store up)
  wakes_left?: number; // wakes left today (a daily cap keeps the credit safe)
  message?: string;
}

export type Phase = 'asleep' | 'starting' | 'services' | 'ready' | 'stopping' | 'unavailable';

/** What the visitor is told, from the function's view of the VM. */
export function phaseOf(s: WakeStatus): Phase {
  if (s.power === 'running') return s.ready ? 'ready' : 'services';
  if (s.power === 'starting') return 'starting';
  if (s.power === 'deallocated' || s.power === 'stopped') return 'asleep';
  if (s.power === 'deallocating' || s.power === 'stopping') return 'stopping';
  return 'unavailable';
}

export class WakeRefused extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WakeRefused';
  }
}

const UNAVAILABLE =
  'The live backend is unavailable right now (its yearly credit may be spent). Everything else on this site still works.';

async function call(cfg: SiteConfig, path: '/api/wake' | '/api/status', method: 'GET' | 'POST'): Promise<WakeStatus> {
  if (!cfg.wakeUrl) throw new WakeRefused(UNAVAILABLE);
  let r: Response;
  try {
    r = await fetch(`${cfg.wakeUrl.replace(/\/+$/, '')}${path}`, { method, credentials: 'omit', cache: 'no-store' });
  } catch {
    throw new WakeRefused(UNAVAILABLE);
  }
  const body = (await r.json().catch(() => ({}))) as Partial<WakeStatus>;
  if (r.status === 429) throw new WakeRefused(body.message ?? 'The live backend has been woken enough times today. Try again tomorrow.');
  if (!r.ok) throw new WakeRefused(body.message ?? UNAVAILABLE);
  return { power: String(body.power ?? 'unknown'), ready: !!body.ready, wakes_left: body.wakes_left, message: body.message };
}

export const requestWake = (cfg: SiteConfig) => call(cfg, '/api/wake', 'POST');
export const getStatus = (cfg: SiteConfig) => call(cfg, '/api/status', 'GET');

/** Where to send the visitor once the backend is ready: its own app, same screen. */
export function backendTarget(cfg: SiteConfig, appPath: string): string | null {
  if (!cfg.backendUrl) return null;
  return `${cfg.backendUrl.replace(/\/+$/, '')}/app/${appPath.replace(/^\/+/, '')}`;
}

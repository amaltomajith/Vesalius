import { afterEach, describe, expect, it, vi } from 'vitest';
import { backendTarget, getStatus, phaseOf, requestWake, WakeRefused } from './wake';

const CFG = { wakeUrl: 'https://wake.example/', backendUrl: 'https://backend.example' };

describe('wake client (lib/wake.ts)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('asks only the configured function, with no body and no credentials', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify({ power: 'starting', ready: false }), { status: 202 }));
    vi.stubGlobal('fetch', fetch);
    expect(await requestWake(CFG)).toMatchObject({ power: 'starting', ready: false });
    await getStatus(CFG);
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(['https://wake.example/api/wake', 'https://wake.example/api/status']);
    for (const [, init] of fetch.mock.calls) {
      expect(init?.body).toBeUndefined(); // nothing about the visitor or a study is ever sent
      expect(init?.credentials).toBe('omit');
    }
  });

  it('turns the daily cap and an unreachable function into plain refusals', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ message: 'enough for today' }), { status: 429 })));
    await expect(requestWake(CFG)).rejects.toThrow('enough for today');
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    await expect(requestWake(CFG)).rejects.toBeInstanceOf(WakeRefused);
  });

  it('never calls anything when the site offers no live backend', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(requestWake({ wakeUrl: null, backendUrl: null })).rejects.toBeInstanceOf(WakeRefused);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('reads the phases the visitor sees from the VM power state', () => {
    expect(phaseOf({ power: 'deallocated', ready: false })).toBe('asleep');
    expect(phaseOf({ power: 'starting', ready: false })).toBe('starting');
    expect(phaseOf({ power: 'running', ready: false })).toBe('services');
    expect(phaseOf({ power: 'running', ready: true })).toBe('ready');
    expect(phaseOf({ power: 'deallocating', ready: false })).toBe('stopping');
    expect(phaseOf({ power: 'unknown', ready: true })).toBe('unavailable');
  });

  it('opens the same screen on the backend', () => {
    expect(backendTarget(CFG, 'pipeline')).toBe('https://backend.example/app/pipeline');
    expect(backendTarget({ ...CFG, backendUrl: 'https://backend.example/' }, '/body')).toBe('https://backend.example/app/body');
    expect(backendTarget({ ...CFG, backendUrl: null }, 'pipeline')).toBeNull();
  });
});

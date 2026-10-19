import { afterEach, describe, expect, it, vi } from 'vitest';
import { basename, landingHref, staticPath } from './runtime';

const ID = '0f6b52aa-1111-4222-8333-444455556666';

describe('static site paths (lib/runtime.ts)', () => {
  it('maps the reads the app makes onto the exported files', () => {
    const base = '/Vesalius/app/';
    expect(staticPath('/demo/datasets', base)).toBe('/Vesalius/app/data/datasets.json');
    expect(staticPath('/demo/proofs/last', base)).toBe('/Vesalius/app/data/proofs/last.json');
    expect(staticPath(`/studies/${ID}/structures`, base)).toBe(`/Vesalius/app/data/studies/${ID}/structures.json`);
    expect(staticPath(`/studies/${ID}/lineage`, base)).toBe(`/Vesalius/app/data/studies/${ID}/lineage.json`);
  });

  it('has no file for anything only the live pipeline can answer', () => {
    for (const p of ['/demo/runs/sample', '/demo/proofs/viewer', '/ai/status', `/series/${ID}`, '/auth/me', '/studies/../etc/structures']) {
      expect(staticPath(p, '/Vesalius/app/')).toBeNull();
    }
  });

  it('derives the router basename and the landing link from the base', () => {
    expect(basename('/app/')).toBe('/app');
    expect(basename('/Vesalius/app/')).toBe('/Vesalius/app');
    expect(landingHref('/app/')).toBe('/');
    expect(landingHref('/Vesalius/app/')).toBe('/Vesalius/');
  });
});

describe('the static build (VITE_STATIC=1)', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  const staticBuild = async () => {
    vi.stubEnv('VITE_STATIC', '1');
    vi.resetModules();
    const fetch = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    return { fetch, api: await import('./api'), auth: await import('../store/authStore'), runtime: await import('./runtime') };
  };

  it('is public and signed out, and never asks a server who is signed in', async () => {
    const { fetch, auth } = await staticBuild();
    await auth.useAuthStore.getState().refresh();
    const s = auth.useAuthStore.getState();
    expect([s.loaded, s.isPublic, s.admin]).toEqual([true, true, false]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('reads exported files and refuses anything that needs the live pipeline, without a request', async () => {
    const { fetch, api, runtime } = await staticBuild();
    await api.api('/demo/datasets');
    expect(fetch).toHaveBeenCalledWith(`${import.meta.env.BASE_URL}data/datasets.json`, {});
    fetch.mockClear();
    await expect(api.api('/demo/runs/sample', { method: 'POST', body: '{}' })).rejects.toBeInstanceOf(runtime.BackendAsleep);
    await expect(api.api(`/series/${ID}`)).rejects.toBeInstanceOf(runtime.BackendAsleep);
    expect(fetch).not.toHaveBeenCalled();
  });
});

// Where the app's data comes from. Two builds of the same code:
//  - served by the pipeline's API (`vite build`, base /app/): every read goes to the API;
//  - the static site on GitHub Pages (`vite build --mode pages`, VITE_STATIC=1): no server at all.
//    The few reads the app makes map onto files exported from gold by scripts/export_static.py
//    (under <base>data/); anything that needs the pipeline (runs, proofs, the AI) is refused with
//    BackendAsleep, and the screens offer to wake the live backend instead (lib/wake.ts).

export const STATIC = import.meta.env.VITE_STATIC === '1';
export const BASE = import.meta.env.BASE_URL; // "/app/" served, "/Vesalius/app/" on Pages
/** three.js's Draco decoder, vendored at build time (scripts/vendor-mediapipe.mjs). Only fetched
 *  for a Draco-compressed GLB: the static site's atlas (scripts/compress_atlas.py). */
export const DRACO_DECODER = `${BASE}draco/`;

/** React Router's basename: the base without its trailing slash ("/app", "/Vesalius/app"). */
export const basename = (base: string = BASE): string => base.replace(/\/+$/, '') || '/';

/** The landing page sits one level above the app ("/" served, "/Vesalius/" on Pages). */
export const landingHref = (base: string = BASE): string => base.replace(/app\/?$/, '') || '/';

export const dataUrl = (path: string, base: string = BASE): string => `${base}data/${path}`;

export class BackendAsleep extends Error {
  constructor() {
    super('This needs the live backend, which is asleep. Wake it to run this.');
    this.name = 'BackendAsleep';
  }
}

const UUID = '[0-9a-fA-F-]{36}';
const STATIC_READS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^\/demo\/datasets$/, () => 'datasets.json'],
  [/^\/demo\/proofs\/last$/, () => 'proofs/last.json'],
  [new RegExp(`^/studies/(${UUID})/structures$`), (m) => `studies/${m[1]}/structures.json`],
  [new RegExp(`^/studies/(${UUID})/lineage$`), (m) => `studies/${m[1]}/lineage.json`],
];

/** The exported file behind an API read, or null when only the live backend can answer it. */
export function staticPath(apiPath: string, base: string = BASE): string | null {
  for (const [pattern, file] of STATIC_READS) {
    const m = apiPath.match(pattern);
    if (m) return dataUrl(file(m), base);
  }
  return null;
}

/** The URL to fetch for a read: the API path itself, or (static site) its exported file. */
export function readUrl(apiPath: string): string {
  if (!STATIC) return apiPath;
  const p = staticPath(apiPath);
  if (!p) throw new BackendAsleep();
  return p;
}

export interface SiteConfig {
  wakeUrl: string | null; // the wake function (deploy/azure/wake); null: no live backend offered
  backendUrl: string | null; // the backend's own site, opened once it is awake
}

let config: Promise<SiteConfig> | null = null;

/** <base>config.json, written by the Pages workflow from repository variables. */
export function loadConfig(): Promise<SiteConfig> {
  config ??= fetch(`${BASE}config.json`)
    .then(async (r) => (r.ok ? ((await r.json()) as Partial<SiteConfig>) : {}))
    .catch(() => ({}))
    .then((c: Partial<SiteConfig>) => ({ wakeUrl: c.wakeUrl || null, backendUrl: c.backendUrl || null }));
  return config;
}

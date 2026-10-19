import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import { summarize, type Summary } from './stats';
import type { StructuresManifest, StudySummary } from '../contract/meshContract';
import { useStudyStore } from '../store/studyStore';
import { dataUrl } from './runtime';

/** A study's manifest: from the API, or (exported studies) from the static files. */
const manifestOf = (s: StudySummary): Promise<StructuresManifest> =>
  s.source === 'static'
    ? fetch(dataUrl(`studies/${s.id}/structures.json`)).then((r) => {
        if (!r.ok) throw new Error(`manifest unavailable (${r.status})`);
        return r.json() as Promise<StructuresManifest>;
      })
    : api<StructuresManifest>(`/studies/${s.id}/structures`);

/** The served studies (GET /studies) and what their structures manifests say, each manifest
 *  validated before it is counted (lib/stats.ts). `summary` is null until the manifests arrive. */
export function useStudyStats() {
  const studies = useStudyStore((s) => s.studies);
  const refresh = useStudyStore((s) => s.refresh);
  const [summary, setSummary] = useState<Summary | null>(null);

  useEffect(() => { void refresh(); }, [refresh]);

  const served = useMemo(() => (studies ?? []).filter((s) => s.source === 'pipeline' || s.source === 'static'), [studies]);

  useEffect(() => {
    if (studies === null) return;
    let live = true;
    void Promise.all(
      served.map((s) =>
        manifestOf(s).then((manifest) => ({ id: s.id, manifest })).catch(() => null),
      ),
    ).then((rows) => { if (live) setSummary(summarize(rows.filter((r) => r !== null))); });
    return () => { live = false; };
  }, [studies, served]);

  return { studies, served, summary };
}

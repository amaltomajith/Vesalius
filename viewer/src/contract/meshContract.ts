// The pipeline's mesh contract (refs/INTEGRATION.md §3) as the viewer consumes it.
// The viewer validates every manifest on load and REFUSES on mismatch -- defence in depth on the
// one failure that matters: a face on screen next to a study identifier (CLAUDE.md hard rule 4).

export const SUPPORTED_MAJOR = 1;

export interface StructureEntry {
  node: string; // the GLB node name as three.js names it (a segmentation label for studies)
  label: string;
  faces: number;
  // measured by the pipeline (studies only; the atlas is not a patient and has none)
  volume_ml?: number;
  hu_mean?: number;
  hu_std?: number;
  bbox_mm?: [number[], number[]];
  // atlas only (assets/atlas/atlas.json)
  name?: string; // the node's original Z-Anatomy name
  path?: string[]; // anatomical grouping, outermost first
  describe?: string; // key into descriptions.json
  landmark?: boolean; // a Z-Anatomy landmark marker, not an organ
}

export interface GroupEntry {
  id: string;
  label: string;
  file: string; // relative to the study, e.g. "mesh/skeletal.glb"
  bytes: number;
  structures: StructureEntry[];
  sha256?: string; // atlas: cache key
  default_on?: boolean; // atlas: visible when the scene opens
}

export interface StructuresManifest {
  contract_version: string;
  kind?: 'atlas'; // absent for pipeline studies
  series_id: string;
  pipeline_version: string;
  segmentation: {
    model: string;
    model_version: string;
    label_set_version: string;
    group_map_version: string;
    weights_sha256?: string;
  };
  geometry: { export_frame: string; units: string; shared_origin: boolean; centring_offset_mm: number[] };
  deface: { required: boolean; applied: boolean; method: string | null; structures_cut?: string | null };
  groups: GroupEntry[];
  not_meshed: { node: string; reason: string }[];
  study: {
    modality: string | null;
    body_part: string | null;
    slice_count: number | null;
    voxel_spacing_mm: number[];
    shifted_study_date: string | null;
  };
}

export class ContractViolation extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ContractViolation';
  }
}

const major = (v: string): number => Number.parseInt(String(v).split('.')[0] ?? '', 10);

/** refs/INTEGRATION.md §3, "Viewer-side validation" -- verbatim rules, fail closed. */
export function validateMeshContract(m: StructuresManifest): void {
  if (!m || typeof m !== 'object') throw new ContractViolation('manifest is not an object');
  if (major(m.contract_version) !== SUPPORTED_MAJOR)
    throw new ContractViolation(`unsupported contract ${m.contract_version}`);
  if (m.geometry?.export_frame !== 'glTF_Y_up_m')
    throw new ContractViolation('expected glTF Y-up metres');
  if (!m.geometry.shared_origin)
    throw new ContractViolation('groups must share an origin or layers will not align');
  if (!m.deface || (m.deface.required && !m.deface.applied))
    throw new ContractViolation('undefaced head study refused');
  if (!Array.isArray(m.groups) || m.groups.length === 0)
    throw new ContractViolation('manifest lists no mesh groups');
}

export interface StudySummary {
  id: string;
  modality: string | null;
  body_part: string | null;
  shifted_study_date: string | null;
  slice_count: number | null;
  thumbnail_artifact_id: string | null;
  source: 'pipeline' | 'demo' | 'atlas' | 'surface';
  credit?: string;
}

export interface LoadedStudy {
  summary: StudySummary;
  manifest: StructuresManifest;
  groupUrl: (g: GroupEntry) => string;
}

const BASE = import.meta.env.BASE_URL; // "/viewer/"

/** The bundled demo study: a public, CC BY chest CT run through this pipeline (see its CREDIT). */
export const DEMO_STUDY: StudySummary = {
  id: 'demo-chest',
  modality: 'CT',
  body_part: 'CHEST',
  shifted_study_date: null,
  slice_count: 133,
  thumbnail_artifact_id: null,
  source: 'demo',
  credit: 'LIDC-IDRI (TCIA), CC BY 3.0, doi:10.7937/K9/TCIA.2015.LO9QL9SX',
};

/** The full-body atlas (Holoscan's 8 systems): a bundled scene, validated like any study. */
export const ATLAS_SCENE: StudySummary = {
  id: 'atlas',
  modality: null,
  body_part: 'WHOLE BODY',
  shifted_study_date: null,
  slice_count: null,
  thumbnail_artifact_id: null,
  source: 'atlas',
  credit: 'Z-Anatomy / BodyParts3D, CC-BY-SA',
};

export async function listStudies(): Promise<StudySummary[]> {
  try {
    const r = await fetch('/studies');
    if (!r.ok) return [DEMO_STUDY];
    const body = (await r.json()) as { studies: Omit<StudySummary, 'source'>[] };
    return [...body.studies.map((s) => ({ ...s, source: 'pipeline' as const })), DEMO_STUDY];
  } catch {
    return [DEMO_STUDY]; // no pipeline instance: the viewer still runs on the bundled study
  }
}

interface GoldArtifact { id: string; kind: string; bytes: number }

/**
 * A bone / skin result (one surface GLB, no structures) as a one-group scene. Built client-side
 * from the series' GOLD manifest (S11), so the defacing check still comes from the pipeline:
 * validateMeshContract refuses it exactly like a structures study when the face was not removed.
 * S11's volume manifest spells the frame "gltf_Y_up_m"; only that exact frame (case-insensitive)
 * is accepted as glTF Y-up metres, anything else fails validation.
 */
async function loadSurface(summary: StudySummary): Promise<LoadedStudy> {
  const get = async <T,>(path: string): Promise<T> => {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`could not load the scan (${r.status})`);
    return (await r.json()) as T;
  };
  const [{ artifacts }, series] = await Promise.all([
    get<{ artifacts: GoldArtifact[] }>(`/series/${summary.id}/artifacts`),
    get<{ body_part: string | null; instance_count: number | null; modality?: string | null }>(`/series/${summary.id}`),
  ]);
  const glb = artifacts.find((a) => a.kind === 'surface_glb');
  const gold = artifacts.find((a) => a.kind === 'manifest');
  if (!glb || !gold) throw new Error('this scan has no surface model in gold');
  const sm = await get<{
    series_id: string;
    pipeline_version: string;
    geometry: { export_frame?: string; centring_offset_mm?: number[]; voxel_spacing_mm?: number[] };
    deface?: { required: boolean; applied: boolean; method: string | null };
  }>(`/demo/files/${gold.id}`);
  const frame = (sm.geometry.export_frame ?? '').toLowerCase() === 'gltf_y_up_m' ? 'glTF_Y_up_m' : String(sm.geometry.export_frame);
  const manifest: StructuresManifest = {
    contract_version: '1.0',
    series_id: sm.series_id,
    pipeline_version: sm.pipeline_version,
    segmentation: { model: 'threshold preset', model_version: '', label_set_version: '', group_map_version: '' },
    geometry: { export_frame: frame, units: 'metres', shared_origin: true, centring_offset_mm: sm.geometry.centring_offset_mm ?? [] },
    deface: sm.deface ?? (undefined as never),
    groups: [{ id: 'surface', label: 'Surface', file: '', bytes: glb.bytes, structures: [] }],
    not_meshed: [],
    study: {
      modality: series.modality ?? null,
      body_part: series.body_part,
      slice_count: series.instance_count,
      voxel_spacing_mm: sm.geometry.voxel_spacing_mm ?? [],
      shifted_study_date: null,
    },
  };
  validateMeshContract(manifest); // no deface block, or required && !applied -> refused
  return { summary, manifest, groupUrl: () => `/demo/files/${glb.id}` };
}

export async function loadStudy(summary: StudySummary): Promise<LoadedStudy> {
  if (summary.source === 'surface') return loadSurface(summary);
  if (summary.source === 'atlas') {
    const root = `${BASE}atlas`;
    const r = await fetch(`${root}/atlas.json`);
    if (!r.ok) throw new Error(`could not load the atlas (${r.status})`);
    const manifest = (await r.json()) as StructuresManifest;
    validateMeshContract(manifest);
    if (manifest.kind !== 'atlas') throw new ContractViolation('not an atlas manifest');
    // atlas files never change for a given hash: the ?v= key lets the browser cache them
    const groupUrl = (g: GroupEntry) => `${root}/${g.file}?v=${(g.sha256 ?? '').slice(0, 12)}`;
    return { summary, manifest, groupUrl };
  }
  const root = summary.source === 'demo' ? `${BASE}demo/chest` : `/studies/${summary.id}`;
  const url = summary.source === 'demo' ? `${root}/structures.json` : `${root}/structures`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`could not load the study manifest (${r.status})`);
  const manifest = (await r.json()) as StructuresManifest;
  validateMeshContract(manifest); // throws ContractViolation -> the study is refused, never shown
  const groupUrl = (g: GroupEntry) =>
    summary.source === 'demo' ? `${root}/${g.file}` : `/studies/${summary.id}/mesh/${g.id}.glb`;
  return { summary, manifest, groupUrl };
}

export async function thumbnailUrl(s: StudySummary): Promise<string | null> {
  if (!s.thumbnail_artifact_id) return null;
  try {
    const r = await fetch(`/artifacts/${s.thumbnail_artifact_id}`);
    if (!r.ok) return null;
    return ((await r.json()) as { url: string }).url;
  } catch {
    return null;
  }
}

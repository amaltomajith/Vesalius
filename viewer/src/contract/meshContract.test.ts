import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ContractViolation,
  listStudies,
  loadStudy,
  validateMeshContract,
  type StructuresManifest,
} from './meshContract';
import { GROUP_COLOURS, useLayerStore } from '../store/layerStore';
import { referenceFor } from '../data/anatomyDatabase';

function manifest(over: Partial<StructuresManifest> = {}): StructuresManifest {
  return {
    contract_version: '1.0',
    series_id: 'sid',
    pipeline_version: '0.1.0',
    segmentation: { model: 'totalsegmentator', model_version: '2.18.0', label_set_version: 'v2', group_map_version: 'v1' },
    geometry: { export_frame: 'glTF_Y_up_m', units: 'metres', shared_origin: true, centring_offset_mm: [0, 0, 0] },
    deface: { required: true, applied: true, method: 'geometric_v1', structures_cut: 's7_region_v1' },
    groups: [
      { id: 'skeletal', label: 'Skeletal', file: 'mesh/skeletal.glb', bytes: 1, structures: [] },
      { id: 'endocrine', label: 'Endocrine', file: 'mesh/endocrine.glb', bytes: 1, structures: [] },
    ],
    not_meshed: [],
    study: { modality: 'CT', body_part: 'HEAD', slice_count: 76, voxel_spacing_mm: [1, 1, 1], shifted_study_date: null },
    ...over,
  };
}

describe('validateMeshContract (refs/INTEGRATION.md §3, fail closed)', () => {
  it('accepts a defaced head study', () => {
    expect(() => validateMeshContract(manifest())).not.toThrow();
  });

  it('accepts a study that never needed defacing', () => {
    expect(() =>
      validateMeshContract(manifest({ deface: { required: false, applied: false, method: 'none' } })),
    ).not.toThrow();
  });

  it('refuses an undefaced head study', () => {
    expect(() =>
      validateMeshContract(manifest({ deface: { required: true, applied: false, method: 'none' } })),
    ).toThrow(/undefaced head study refused/);
  });

  it('refuses a manifest with no deface block at all', () => {
    const m = manifest();
    delete (m as Partial<StructuresManifest>).deface;
    expect(() => validateMeshContract(m)).toThrow(ContractViolation);
  });

  it.each([
    ['another major version', { contract_version: '2.0' }, /unsupported contract/],
    ['another frame', { geometry: { ...manifest().geometry, export_frame: 'LPS_mm' } }, /Y-up/],
    ['no shared origin', { geometry: { ...manifest().geometry, shared_origin: false } }, /share an origin/],
    ['no groups', { groups: [] }, /no mesh groups/],
  ])('refuses %s', (_name, over, msg) => {
    expect(() => validateMeshContract(manifest(over as Partial<StructuresManifest>))).toThrow(msg);
  });

  it('accepts a newer minor version of the same major', () => {
    expect(() => validateMeshContract(manifest({ contract_version: '1.3' }))).not.toThrow();
  });
});

describe('loadStudy', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('never hands back a refused study (so nothing can render it)', async () => {
    const bad = manifest({ deface: { required: true, applied: false, method: 'none' } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(bad), { status: 200 })));
    await expect(
      loadStudy({ id: 'x', modality: 'CT', body_part: 'HEAD', shifted_study_date: null, slice_count: 1, thumbnail_artifact_id: null, source: 'pipeline' }),
    ).rejects.toBeInstanceOf(ContractViolation);
  });

  it('loads an exported study from its static files, and still refuses an uncut face', async () => {
    const base = import.meta.env.BASE_URL;
    const fetch = vi.fn(async () => new Response(JSON.stringify(manifest()), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const study = { id: 'abc', modality: 'CT', body_part: 'HEAD', shifted_study_date: null, slice_count: 1, thumbnail_artifact_id: null, source: 'static' as const };
    const s = await loadStudy(study);
    expect(fetch).toHaveBeenCalledWith(`${base}data/studies/abc/structures.json`);
    expect(s.groupUrl(s.manifest.groups[0])).toBe(`${base}data/studies/abc/mesh/skeletal.glb`);

    const bad = manifest({ deface: { required: true, applied: false, method: 'none' } });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(bad), { status: 200 })));
    await expect(loadStudy(study)).rejects.toBeInstanceOf(ContractViolation);
  });

  it('lists the exported studies when there is no pipeline to ask', async () => {
    const index = { studies: [{ id: 'abc', modality: 'CT', body_part: 'HEAD', shifted_study_date: null, slice_count: 1, thumbnail_artifact_id: 't', credit: '3D Slicer' }] };
    vi.stubGlobal('fetch', vi.fn(async (url: string) =>
      url === '/studies' ? new Response('', { status: 502 }) : new Response(JSON.stringify(index), { status: 200 })));
    const studies = await listStudies();
    expect(studies.map((s) => [s.id, s.source, s.credit])).toEqual([['abc', 'static', '3D Slicer']]);
  });

  it('resolves pipeline groups through the gold-only study route', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(manifest()), { status: 200 })));
    const s = await loadStudy({ id: 'abc', modality: 'CT', body_part: 'HEAD', shifted_study_date: null, slice_count: 1, thumbnail_artifact_id: null, source: 'pipeline' });
    expect(s.groupUrl(s.manifest.groups[0])).toBe('/studies/abc/mesh/skeletal.glb');
  });
});

describe('layer store (INTEGRATION §4.2)', () => {
  it('builds groups from the manifest with stable colours and lazy non-default groups', () => {
    useLayerStore.getState().setFromManifest('k', manifest(), (g) => `/u/${g.id}`);
    const { order, groups } = useLayerStore.getState();
    expect(order).toEqual(['skeletal', 'endocrine']);
    expect(groups.skeletal.color).toBe(GROUP_COLOURS.skeletal.color);
    expect(groups.skeletal.visible && groups.skeletal.requested).toBe(true);
    expect(groups.endocrine.visible || groups.endocrine.requested).toBe(false);
    useLayerStore.getState().toggle('endocrine');
    expect(useLayerStore.getState().groups.endocrine.requested).toBe(true);
  });
});

describe('anatomy enrichment (INTEGRATION §4.3)', () => {
  it('matches exact labels, then the longest family prefix, else nothing', () => {
    expect(referenceFor('liver')?.typicalVolumeMl).toBeDefined();
    expect(referenceFor('rib_left_3')?.function).toMatch(/thoracic/);
    expect(referenceFor('lung_upper_lobe_left')?.typicalHu).toBeDefined();
    expect(referenceFor('no_such_label')).toBeNull();
  });
});

describe('atlas and surface scenes (phase three)', () => {
  afterEach(() => vi.unstubAllGlobals());

  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

  it('loads the atlas through the same validation, with hash-keyed group URLs', async () => {
    const m = manifest({ kind: 'atlas', deface: { required: false, applied: false, method: 'not_a_patient' } });
    m.groups[0].sha256 = 'abcdef0123456789';
    vi.stubGlobal('fetch', vi.fn(async () => json(m)));
    const s = await loadStudy({ id: 'atlas', modality: null, body_part: 'WHOLE BODY', shifted_study_date: null, slice_count: null, thumbnail_artifact_id: null, source: 'atlas' });
    expect(s.groupUrl(s.manifest.groups[0])).toBe('/app/atlas/mesh/skeletal.glb?v=abcdef012345');
  });

  it('refuses an atlas file that is not marked as the atlas', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(manifest({ deface: { required: false, applied: false, method: 'none' } }))));
    await expect(
      loadStudy({ id: 'atlas', modality: null, body_part: null, shifted_study_date: null, slice_count: null, thumbnail_artifact_id: null, source: 'atlas' }),
    ).rejects.toBeInstanceOf(ContractViolation);
  });

  function surfaceFetch(deface: unknown, frame = 'gltf_Y_up_m') {
    return vi.fn(async (url: string) => {
      if (url.endsWith('/artifacts')) return json({ artifacts: [{ id: 'g1', kind: 'surface_glb', bytes: 10 }, { id: 'm1', kind: 'manifest', bytes: 5 }] });
      if (url.startsWith('/series/')) return json({ body_part: 'HEAD', instance_count: 106, modality: 'CT' });
      return json({ series_id: 's', pipeline_version: '0.1.0', geometry: { export_frame: frame, centring_offset_mm: [0, 0, 0] }, deface });
    });
  }
  const surface = { id: 's', modality: null, body_part: null, shifted_study_date: null, slice_count: null, thumbnail_artifact_id: null, source: 'surface' as const };

  it('opens a defaced bone / skin result from its gold manifest', async () => {
    vi.stubGlobal('fetch', surfaceFetch({ required: true, applied: true, method: 'geometric_v1' }));
    const s = await loadStudy(surface);
    expect(s.manifest.geometry.export_frame).toBe('glTF_Y_up_m');
    expect(s.groupUrl(s.manifest.groups[0])).toBe('/demo/files/g1');
  });

  it('refuses a bone / skin result whose face was not removed', async () => {
    vi.stubGlobal('fetch', surfaceFetch({ required: true, applied: false, method: 'none' }));
    await expect(loadStudy(surface)).rejects.toThrow(/undefaced head study refused/);
  });

  it('refuses a bone / skin result with no deface block or another frame', async () => {
    vi.stubGlobal('fetch', surfaceFetch(undefined));
    await expect(loadStudy(surface)).rejects.toBeInstanceOf(ContractViolation);
    vi.stubGlobal('fetch', surfaceFetch({ required: false, applied: false, method: 'none' }, 'LPS_mm'));
    await expect(loadStudy(surface)).rejects.toThrow(/Y-up/);
  });
});

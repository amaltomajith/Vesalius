import { describe, expect, it } from 'vitest';
import type { GroupEntry, StructuresManifest } from '../contract/meshContract';
import { bySystem, summarize, SYSTEM_COLOUR, SYSTEMS, totals } from './stats';

const group = (id: string, items: [string, number][]): GroupEntry => ({
  id,
  label: id,
  file: `mesh/${id}.glb`,
  bytes: 1,
  structures: items.map(([label, ml]) => ({ node: label.toLowerCase(), label, faces: 10, volume_ml: ml })),
});

function manifest(body: string, groups: GroupEntry[], over: Partial<StructuresManifest> = {}): StructuresManifest {
  return {
    contract_version: '1.0',
    series_id: body,
    pipeline_version: '0.1.0',
    segmentation: { model: 'totalsegmentator', model_version: '2.18.0', label_set_version: 'v2', group_map_version: 'v1' },
    geometry: { export_frame: 'glTF_Y_up_m', units: 'metres', shared_origin: true, centring_offset_mm: [0, 0, 0] },
    deface: { required: false, applied: false, method: 'none' },
    groups,
    not_meshed: [],
    study: { modality: 'CT', body_part: body, slice_count: 100, voxel_spacing_mm: [1, 1, 1], shifted_study_date: null },
    ...over,
  };
}

const chest = manifest('CHEST', [
  group('skeletal', [['Rib 1', 5], ['Sternum', 40]]),
  group('cardiovascular', [['Heart', 600], ['Aorta', 120]]),
  group('respiratory', [['Lung left', 1800]]),
  group('other', [['Something', 3]]),
]);
const head = manifest('HEAD', [group('skeletal', [['Skull', 598]])], {
  deface: { required: true, applied: true, method: 'geometric_v1', structures_cut: 's7_region_v1' },
});
const undefaced = manifest('HEAD', [group('skeletal', [['Skull', 700]])], {
  deface: { required: true, applied: false, method: 'none' },
});

describe('summarize: the dashboard counts only what the contract accepts', () => {
  const s = summarize([
    { id: 'c', manifest: chest },
    { id: 'h', manifest: head },
    { id: 'x', manifest: undefaced },
  ]);

  it('refuses an undefaced head study and leaves it out of every number', () => {
    expect(s.refused).toEqual(['x']);
    expect(s.studies.map((t) => t.id)).toEqual(['c', 'h']);
    expect(totals(s.studies).structures).toBe(7); // 6 chest (incl. "other") + 1 head, not the refused skull
  });

  it('counts structures per system, in the fixed system order', () => {
    const rows = bySystem(s.studies);
    expect(rows.map((r) => r.system)).toEqual([...SYSTEMS]);
    expect(rows.find((r) => r.system === 'skeletal')!.count).toBe(3);
    expect(rows.find((r) => r.system === 'cardiovascular')!.count).toBe(2);
    expect(rows.find((r) => r.system === 'nervous')!.count).toBe(0);
  });

  it('filters to one study, and names the largest structures first', () => {
    const rows = bySystem(s.studies, 'c');
    expect(rows.find((r) => r.system === 'skeletal')!.count).toBe(2);
    expect(rows.find((r) => r.system === 'cardiovascular')!.examples).toEqual(['Heart', 'Aorta']);
  });

  it('finds the largest structure by measured volume', () => {
    expect(totals(s.studies).largest).toEqual({ label: 'Lung left', ml: 1800 });
    expect(totals(s.studies, 'h').largest).toEqual({ label: 'Skull', ml: 598 });
  });

  it('tallies defacing: required, and applied where required', () => {
    const t = totals(s.studies);
    expect(t.defaceRequired).toBe(1);
    expect(t.defaceApplied).toBe(1);
    expect(totals(s.studies, 'c').defaceRequired).toBe(0);
  });

  it('gives every system a colour, and no two systems the same one', () => {
    const colours = SYSTEMS.map((k) => SYSTEM_COLOUR[k]);
    expect(colours.every((c) => /^#[0-9a-f]{6}$/.test(c))).toBe(true);
    expect(new Set(colours).size).toBe(SYSTEMS.length);
  });
});

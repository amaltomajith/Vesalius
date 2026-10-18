// What the dashboard says about the served studies, computed from their structures manifests.
// Pure: every number here traces to a manifest field; a manifest the contract refuses is counted
// as refused and contributes no data (fail closed, as the viewer does).

import { validateMeshContract, type StructuresManifest } from '../contract/meshContract';

/** The systems in the chart's fixed order. The order is part of the palette: it is the order
 *  whose adjacent colours passed the dataviz validator (see SYSTEM_COLOUR). */
export const SYSTEMS = [
  'skeletal', 'cardiovascular', 'respiratory', 'visceral', 'endocrine', 'nervous', 'muscular',
] as const;
export type System = (typeof SYSTEMS)[number];

/** One hue per system, the viewer's identity hues re-stepped for the light card (#f1f5f2).
 *  validate_palette.js --mode light: all checks pass in this order; CVD 6.9 (warn band) and four
 *  slots under 3:1, so every bar carries a direct label and the chart has a table view. */
export const SYSTEM_COLOUR: Record<System, string> = {
  skeletal: '#2a78d6',
  cardiovascular: '#e34948',
  respiratory: '#1baf7a',
  visceral: '#eb6834',
  endocrine: '#6250d6',
  nervous: '#eda100',
  muscular: '#e87ba4',
};

export const SYSTEM_LABEL: Record<System, string> = {
  skeletal: 'Skeletal',
  cardiovascular: 'Cardiovascular',
  respiratory: 'Respiratory',
  visceral: 'Visceral',
  endocrine: 'Endocrine',
  nervous: 'Nervous',
  muscular: 'Muscular',
};

export interface StudyInput {
  id: string;
  manifest: StructuresManifest;
}

export interface StudyStats {
  id: string;
  bodyPart: string; // "Chest", from the manifest's study block
  total: number;
  perSystem: Record<System, number>;
  other: number; // structures in groups outside SYSTEMS (the pipeline's "other")
  names: Record<System, string[]>; // labels, largest first
  largest: { label: string; ml: number } | null;
  defaceRequired: boolean;
  defaceApplied: boolean;
}

export interface Summary {
  studies: StudyStats[];
  refused: string[]; // ids whose manifest the contract refused
}

const isSystem = (g: string): g is System => (SYSTEMS as readonly string[]).includes(g);

const title = (s: string | null) => (s ? s.charAt(0) + s.slice(1).toLowerCase() : 'Unknown');

export function studyStats(id: string, m: StructuresManifest): StudyStats {
  const perSystem = Object.fromEntries(SYSTEMS.map((s) => [s, 0])) as Record<System, number>;
  const names = Object.fromEntries(SYSTEMS.map((s) => [s, [] as string[]])) as Record<System, string[]>;
  let other = 0;
  let largest: StudyStats['largest'] = null;
  for (const g of m.groups) {
    const sorted = [...g.structures].sort((a, b) => (b.volume_ml ?? 0) - (a.volume_ml ?? 0));
    if (isSystem(g.id)) {
      perSystem[g.id] += g.structures.length;
      names[g.id].push(...sorted.map((s) => s.label));
    } else {
      other += g.structures.length;
    }
    for (const s of g.structures) {
      if (s.volume_ml !== undefined && (!largest || s.volume_ml > largest.ml)) largest = { label: s.label, ml: s.volume_ml };
    }
  }
  const total = SYSTEMS.reduce((n, s) => n + perSystem[s], 0) + other;
  return {
    id,
    bodyPart: title(m.study?.body_part ?? null),
    total,
    perSystem,
    other,
    names,
    largest,
    defaceRequired: !!m.deface?.required,
    defaceApplied: !!m.deface?.applied,
  };
}

/** Every served study's numbers; refused manifests are listed, never counted. */
export function summarize(inputs: StudyInput[]): Summary {
  const studies: StudyStats[] = [];
  const refused: string[] = [];
  for (const { id, manifest } of inputs) {
    try {
      validateMeshContract(manifest);
    } catch {
      refused.push(id);
      continue;
    }
    studies.push(studyStats(id, manifest));
  }
  return { studies, refused };
}

/** The chart's view: the chosen studies (all when `only` is null) folded into one row per system. */
export function bySystem(studies: StudyStats[], only: string | null = null) {
  const picked = only ? studies.filter((s) => s.id === only) : studies;
  return SYSTEMS.map((system) => ({
    system,
    count: picked.reduce((n, s) => n + s.perSystem[system], 0),
    // a few names for the tooltip: the largest structures of the picked studies, no repeats
    examples: [...new Set(picked.flatMap((s) => s.names[system]))].slice(0, 4),
  }));
}

export function totals(studies: StudyStats[], only: string | null = null) {
  const picked = only ? studies.filter((s) => s.id === only) : studies;
  const largest = picked.reduce<StudyStats['largest']>(
    (best, s) => (s.largest && (!best || s.largest.ml > best.ml) ? s.largest : best),
    null,
  );
  return {
    structures: picked.reduce((n, s) => n + s.total, 0),
    studies: picked.length,
    largest,
    defaceRequired: picked.filter((s) => s.defaceRequired).length,
    defaceApplied: picked.filter((s) => s.defaceRequired && s.defaceApplied).length,
  };
}

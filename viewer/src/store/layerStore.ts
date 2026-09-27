import { create } from 'zustand';
import type { GroupEntry, StructuresManifest } from '../contract/meshContract';

// Groups now come from each study's manifest (refs/INTEGRATION.md §4.2): an abdomen CT has no
// `nervous` group. Colours are a stable map keyed by group id, so `visceral` is always the same
// orange whichever groups a study contains. Group colours belong to the layer system, not the
// UI chrome (refs/VIEWER-CONSTRAINTS.md, design tokens).
export const GROUP_COLOURS: Record<string, { color: string; emissive: string }> = {
  skeletal:       { color: '#C0C8D0', emissive: '#8899AA' },
  cardiovascular: { color: '#FF4466', emissive: '#FF0033' },
  visceral:       { color: '#FF8844', emissive: '#FF6600' },
  respiratory:    { color: '#44FFEE', emissive: '#00CCBB' },
  muscular:       { color: '#FF6688', emissive: '#FF3355' },
  nervous:        { color: '#FFEE44', emissive: '#FFCC00' },
  endocrine:      { color: '#4488FF', emissive: '#0055FF' },
  // Holoscan's atlas systems keep Holoscan's colours
  lymphoid:       { color: '#44FFEE', emissive: '#00CCBB' },
  joints:         { color: '#4488FF', emissive: '#0055FF' },
  regions:        { color: '#8090A0', emissive: '#303C48' },
  other:          { color: '#8090A0', emissive: '#303C48' },
  surface:        { color: '#EDE6D6', emissive: '#8A8272' }, // a bone / skin result
};
const FALLBACK_COLOUR = GROUP_COLOURS.other;

// Visible on load. The rest are fetched only when first toggled on: pipeline studies are
// per-patient and not cacheable across studies (VIEWER-CONSTRAINTS "Loading behaviour").
const DEFAULT_ON = new Set(['skeletal', 'cardiovascular', 'visceral', 'nervous']);

export interface GroupLayer {
  id: string;
  label: string;
  url: string;
  color: string;
  emissive: string;
  visible: boolean;
  requested: boolean; // has ever been visible -> its GLB has been (or is being) fetched
  loaded: boolean;
  structureCount: number;
}

interface LayerState {
  studyKey: string | null;
  order: string[];
  groups: Record<string, GroupLayer>;
  setFromManifest: (
    studyKey: string,
    m: StructuresManifest,
    urlFor: (g: GroupEntry) => string,
  ) => void;
  toggle: (id: string) => void;
  markLoaded: (id: string) => void;
  clear: () => void;
}

export const useLayerStore = create<LayerState>((set) => ({
  studyKey: null,
  order: [],
  groups: {},
  setFromManifest: (studyKey, m, urlFor) => {
    const groups: Record<string, GroupLayer> = {};
    const anyDefault = m.groups.some((g) => DEFAULT_ON.has(g.id));
    for (const g of m.groups) {
      const c = GROUP_COLOURS[g.id] ?? FALLBACK_COLOUR;
      // the atlas says which systems open visible (Holoscan: skeletal, cardiovascular, visceral)
      const on = g.default_on ?? (anyDefault ? DEFAULT_ON.has(g.id) : true);
      groups[g.id] = {
        id: g.id,
        label: g.label,
        url: urlFor(g),
        color: c.color,
        emissive: c.emissive,
        visible: on,
        requested: on,
        loaded: false,
        structureCount: g.structures.length,
      };
    }
    set({ studyKey, order: m.groups.map((g) => g.id), groups });
  },
  toggle: (id) =>
    set((state) => {
      const g = state.groups[id];
      if (!g) return state;
      return {
        groups: { ...state.groups, [id]: { ...g, visible: !g.visible, requested: true } },
      };
    }),
  markLoaded: (id) =>
    set((state) => {
      const g = state.groups[id];
      if (!g || g.loaded) return state;
      return { groups: { ...state.groups, [id]: { ...g, loaded: true } } };
    }),
  clear: () => set({ studyKey: null, order: [], groups: {} }),
}));

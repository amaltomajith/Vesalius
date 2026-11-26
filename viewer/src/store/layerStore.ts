import { create } from 'zustand';
import type { GroupEntry, StructuresManifest } from '../contract/meshContract';

// Groups now come from each study's manifest (refs/INTEGRATION.md §4.2): an abdomen CT has no
// `nervous` group. Colours are a stable map keyed by group id, so `visceral` is always the same
// colour whichever groups a study contains. Group colours belong to the layer system, not the
// UI chrome (refs/VIEWER-CONSTRAINTS.md, design tokens).
//
// The anatomical palette (2026-10-18, the room redesign): ivory bone as on the landing's statue,
// deep red vessels, coral organs, yellow nerves... chosen within each system's anatomical hue and
// checked with the dataviz palette validator on the hall surface (#060b09), all pairs that can be
// on screen together (the atlas's 8 systems; a study's 7): normal-vision floor PASS (worst 15.5),
// CVD separation PASS (worst 8.0). Deliberate exceptions, relieved by the names always shown in
// the layer list, the hover label and the part card: bone is ivory (outside the chart lightness
// band, low chroma) and vessels sit at 2.99:1 against the hall. The emissive is the colour itself,
// so a selected part glows in its own system's colour, never a neon twin.
const tone = (color: string) => ({ color, emissive: color });
export const GROUP_COLOURS: Record<string, { color: string; emissive: string }> = {
  skeletal:       tone('#e8dfc8'),
  cardiovascular: tone('#934542'),
  visceral:       tone('#dc9769'),
  respiratory:    tone('#28c3b8'),
  muscular:       tone('#dc5481'),
  nervous:        tone('#dccc23'),
  endocrine:      tone('#775bac'),
  lymphoid:       tone('#338e3a'),
  joints:         tone('#0983df'),
  regions:        tone('#8048b6'),
  other:          tone('#8a9a94'),
  surface:        tone('#ede6d6'), // a bone / skin result
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

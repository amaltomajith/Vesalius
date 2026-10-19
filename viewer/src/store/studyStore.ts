import { create } from 'zustand';
import {
  ATLAS_SCENE,
  ContractViolation,
  listStudies,
  loadStudy,
  type LoadedStudy,
  type StructureEntry,
  type StudySummary,
} from '../contract/meshContract';
import { useLayerStore } from './layerStore';
import { STATIC } from '../lib/runtime';

export interface StructureInfo extends StructureEntry {
  groupId: string;
  groupLabel: string;
}

export interface SceneError {
  studyId: string;
  message: string;
  refused: boolean; // a contract violation: the scene is refused and nothing renders
}

interface SceneState {
  studies: StudySummary[] | null;
  current: LoadedStudy | null;
  loading: string | null; // id of the scene being opened
  error: SceneError | null;
  // node name (as three.js names it) -> manifest entry, for the open scene
  structures: Record<string, StructureInfo>;
  // atlas: Z-Anatomy descriptions, loaded on first need
  descriptions: Record<string, string> | null;
  refresh: () => Promise<StudySummary[]>;
  open: (s: StudySummary) => Promise<void>;
  openById: (id: string) => Promise<void>;
  loadDescriptions: () => Promise<void>;
}

export const useStudyStore = create<SceneState>((set, get) => ({
  studies: null,
  current: null,
  loading: null,
  error: null,
  structures: {},
  descriptions: null,
  refresh: async () => {
    const studies = await listStudies();
    set({ studies });
    return studies;
  },
  open: async (s) => {
    if (get().loading === s.id) return;
    const cur = get().current;
    if (cur && cur.summary.id === s.id && cur.summary.source === s.source) return;
    set({ loading: s.id, error: null });
    try {
      const loaded = await loadStudy(s);
      const structures: Record<string, StructureInfo> = {};
      for (const g of loaded.manifest.groups)
        for (const st of g.structures)
          structures[st.node] = { ...st, groupId: g.id, groupLabel: g.label };
      useLayerStore.getState().setFromManifest(`${s.source}:${s.id}`, loaded.manifest, loaded.groupUrl);
      set({ current: loaded, structures, loading: null });
    } catch (err) {
      // Fail closed: a manifest that violates the contract is refused and nothing is rendered.
      useLayerStore.getState().clear();
      set({
        current: null,
        structures: {},
        loading: null,
        error: {
          studyId: s.id,
          message: err instanceof Error ? err.message : String(err),
          refused: err instanceof ContractViolation,
        },
      });
    }
  },
  openById: async (id) => {
    if (id === ATLAS_SCENE.id) return get().open(ATLAS_SCENE);
    if (id.startsWith('surface:')) {
      const sid = id.slice('surface:'.length);
      return get().open({
        id: sid, modality: null, body_part: null, shifted_study_date: null, slice_count: null,
        thumbnail_artifact_id: null, source: 'surface',
      });
    }
    const studies = get().studies ?? (await get().refresh());
    const found = studies.find((s) => s.id === id);
    return get().open(
      found ?? {
        id,
        modality: null,
        body_part: null,
        shifted_study_date: null,
        slice_count: null,
        thumbnail_artifact_id: null,
        source: STATIC ? 'static' : 'pipeline',
      },
    );
  },
  loadDescriptions: async () => {
    if (get().descriptions) return;
    try {
      const r = await fetch(`${import.meta.env.BASE_URL}atlas/descriptions.json`);
      if (r.ok) set({ descriptions: (await r.json()) as Record<string, string> });
    } catch {
      /* descriptions are enrichment: the panel falls back to the structure's own facts */
    }
  },
}));

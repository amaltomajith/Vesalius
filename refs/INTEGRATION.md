# Integration — Phantom → Holoscan

How the pipeline feeds the viewer. **Read this before changing either system.**

---

## 1. What changes and why

Holoscan currently loads eight static GLB files of a generic body. Phantom produces GLB from
real patient scans. Connecting them turns an anatomy atlas into a patient-specific viewer.

Two roadmap items on the viewer close as a side effect: simulated vitals become real study
metadata, and the ten hand-written anatomy database entries stop being a data-entry problem
because structure names arrive from segmentation.

### The blocker

Holoscan's entire interaction model depends on `mesh.name` — raycast hits a mesh, look up the
name, populate the panel. Phantom's current output is a **threshold-derived surface**: mesh the
bone preset and you get one undifferentiated skeleton with arbitrary component names. Point at
it and there is nothing to name.

**Everything in this document follows from fixing that.**

---

## 2. Pipeline changes

### S8 gains a second mode

```
S8.mode = "threshold"   existing: HU preset -> one binary mask
        | "structures"  new: anatomical segmentation -> N labelled masks
```

**Structure mode** runs an anatomical segmentation model producing per-structure masks with
known labels. `TotalSegmentator` is the leading candidate — nnU-Net based, segments ~117
structures from CT.

> **Verify licensing before committing.** Code and model weights are not necessarily under the
> same terms, and commercial use may require a separate licence. Confirm this in week one, not
> after building on it.
>
> **Fallbacks:** MONAI bundle segmentation models; a smaller nnU-Net trained on a public organ
> dataset; or threshold-plus-region-growing for a hand-picked subset (liver, kidneys, spleen,
> lungs, skeleton). A working ten-structure pipeline beats a blocked 117-structure one.

Structure mode is **CT-only**, same constraint as the HU presets.

### S8b — Group mapping (new)

Segmentation emits a flat list of ~117 labels. The viewer needs system groups, because its
layer panel toggles systems and its `SystemLayer` component loads one GLB per system.

Map labels to groups via a versioned table:

```python
GROUP_MAP_V1 = {
    "skeletal":       ["vertebrae_*", "rib_*", "humerus_*", "femur_*", "hip_*",
                       "scapula_*", "clavicula_*", "sacrum", "skull", "sternum"],
    "cardiovascular": ["heart*", "aorta", "pulmonary_artery", "iliac_artery_*",
                       "iliac_vena_*", "inferior_vena_cava", "portal_vein_and_splenic_vein"],
    "visceral":       ["liver", "spleen", "kidney_*", "stomach", "pancreas", "gallbladder",
                       "duodenum", "small_bowel", "colon", "urinary_bladder", "esophagus"],
    "respiratory":    ["lung_*", "trachea"],
    "muscular":       ["autochthon_*", "iliopsoas_*", "gluteus_*"],
    "nervous":        ["brain", "spinal_cord"],
    "endocrine":      ["adrenal_gland_*", "thyroid_gland"],
}
```

Unmapped labels go to a group named `other` — never dropped. Record the map version in
`transform_runs`; it changes output structure, so it is a versioned parameter.

**Note the group set differs from the viewer's current eight.** `lymphoid`, `joints`, and
`regions` have no segmentation equivalent; `respiratory` and `endocrine` are new. The viewer
must derive its layer list from the manifest rather than hardcoding — see §4.

### S9 — Per-structure meshing

Marching cubes runs **once per structure mask**, not once per volume. Each output carries its
label.

For masks, pass the mask through a light Gaussian blur first and mesh at level 0.5 — marching
cubes on a hard binary mask produces blocky stair-stepping.

**The defacing gate still applies, and matters more here.** In the pipeline an undefaced head
mesh is a file. In the viewer it is a face on screen, rotatable, beside a study identifier.
S7 gates S9 exactly as before, no exceptions for structure mode.

### S10 — Per-structure decimation budgets

A 117-structure patient at full marching-cubes resolution is hundreds of megabytes. The
viewer loads over a network.

```python
FACE_BUDGET_V1 = {
    "skeletal":        120_000,   # complex, shared across many bones
    "cardiovascular":   60_000,
    "visceral":         80_000,
    "respiratory":      40_000,
    "muscular":         40_000,
    "nervous":          30_000,
    "endocrine":        10_000,
    "other":            20_000,
}
```

Budget is **per group**, allocated across that group's structures proportionally to their
pre-decimation face counts. Small structures keep a floor of ~800 faces so they stay
recognisable.

**Target: total payload under 25 MB across all groups.** The existing viewer ships 163 MB of
static models and gets away with it because they are cached; per-patient models are not.
Enable Draco compression here.

### S11 — Multi-node GLB export by group

One GLB **per group**, each containing one named node per structure.

This deliberately preserves the viewer's existing `SystemLayer` architecture — one GLB per
system, toggled by visibility. Minimal rework.

```
gold/{series_id}/
    mesh/skeletal.glb          nodes: vertebrae_T7, rib_left_4, ...
    mesh/cardiovascular.glb    nodes: heart, aorta, ...
    mesh/visceral.glb          nodes: liver, kidney_left, ...
    mesh/structures.json       the manifest — see §3
    volume.nii.gz
    thumbnail.png
    manifest.json
```

**Node naming is the contract.** Node name is exactly the segmentation label, unmodified:
`kidney_left`, not `Kidney_Left` or `kidneyl`. Lowercase, underscore-separated, stable across
runs. The viewer keys on this.

Apply the LPS→glTF conversion once at group level so every group shares an origin — otherwise
the systems will not align when layered.

---

## 3. The mesh contract

Published as `mesh_contract` v1.0 alongside the existing `volume_contract`.

### `structures.json`

```json
{
  "contract_version": "1.0",
  "series_id": "...",
  "pipeline_version": "0.4.0",
  "segmentation": {
    "model": "totalsegmentator",
    "model_version": "...",
    "label_set_version": "v1",
    "group_map_version": "v1"
  },
  "geometry": {
    "export_frame": "glTF_Y_up_m",
    "units": "metres",
    "shared_origin": true,
    "centring_offset_mm": [-12.4, 88.1, -204.7]
  },
  "deface": { "required": true, "applied": true, "method": "geometric_v1" },
  "groups": [
    {
      "id": "visceral",
      "label": "Visceral",
      "file": "mesh/visceral.glb",
      "bytes": 3145728,
      "structures": [
        {
          "node": "liver",
          "label": "Liver",
          "faces": 24180,
          "volume_ml": 1486.2,
          "hu_mean": 54.1,
          "hu_std": 12.8,
          "bbox_mm": [[...], [...]]
        },
        {
          "node": "kidney_left",
          "label": "Left Kidney",
          "faces": 9040,
          "volume_ml": 158.7,
          "hu_mean": 33.4,
          "hu_std": 9.2,
          "bbox_mm": [[...], [...]]
        }
      ]
    }
  ],
  "study": {
    "modality": "CT",
    "body_part": "ABDOMEN",
    "slice_count": 412,
    "voxel_spacing_mm": [0.7, 0.7, 0.7],
    "shifted_study_date": "2019-04-02"
  }
}
```

**The per-structure measurements are not decoration.** They replace the viewer's simulated
vitals and they reground the AI insight — see §5. Compute them in S8 from the mask and the
volume, before meshing, where they are cheap and exact.

### Viewer-side validation

The viewer validates on load and refuses on mismatch, same principle as the CV consumer:

```ts
function validateMeshContract(m: StructuresManifest): void {
  if (major(m.contract_version) !== SUPPORTED_MAJOR)
    throw new ContractViolation(`unsupported contract ${m.contract_version}`);
  if (m.geometry.export_frame !== "glTF_Y_up_m")
    throw new ContractViolation("expected glTF Y-up metres");
  if (!m.geometry.shared_origin)
    throw new ContractViolation("groups must share an origin or layers will not align");
  if (m.deface.required && !m.deface.applied)
    throw new ContractViolation("undefaced head study refused");
}
```

That last check is worth writing even though S7 already enforces it. Defence in depth on the
one failure that matters, and it makes the gate visible in the product rather than only in
the pipeline.

---

## 4. Viewer changes

Read `VIEWER-CONSTRAINTS.md` before touching any of this — several things in the existing
code look removable and are not.

### 4.1 Study selection (new)

A study picker before the hologram loads. Calls `GET /studies`, shows modality, body part,
date, thumbnail. Selecting one fetches its `structures.json`.

Keep a **demo study** bundled so the viewer still runs without a pipeline instance.

### 4.2 Dynamic layers — replaces the fixed eight

`layerStore.ts` currently holds eight hardcoded booleans. Groups now come from the manifest
and vary per study — an abdomen CT has no `nervous` group.

```ts
interface LayerState {
  groups: Record<string, { label: string; visible: boolean; color: string; file: string }>;
  setFromManifest(m: StructuresManifest): void;
  toggle(id: string): void;
}
```

Colour assignment moves to a stable map keyed by group id, so `visceral` is always the same
orange regardless of which groups a study contains.

`LayerPanel.tsx` iterates `groups` rather than a literal array. `SystemLayer.tsx` takes a URL
prop instead of a static path.

### 4.3 Anatomy data — manifest first, static enrichment second

`anatomyDatabase.ts` currently maps ten mesh names by hand, with a name-prettifying fallback.
Invert it:

1. **Manifest** supplies node name, display label, and measurements — always present
2. **Static database** optionally enriches with function, location, clinical note, keyed on
   the segmentation label vocabulary
3. **Fallback** stays for anything unmatched

The static database is now worth maintaining, because the label vocabulary is a **fixed,
known set** rather than hundreds of arbitrary GLB node names. Writing entries for ~40 common
structures covers most of what anyone will point at.

### 4.4 Real vitals

The vitals panel's simulated heart rate and temperature are replaced by study facts: modality,
body part, slice count, voxel spacing, structure count, shifted study date.

**Add a provenance line.** Pipeline version and series ID, displayed in the HUD. It makes
lineage visible in the product, which is the whole argument of the pipeline — and it costs one
line of UI.

### 4.5 Selection still works unchanged

Raycast → `mesh.name` → now a segmentation label → manifest lookup. The gesture engine, freeze
mode, victory-select, fist-dismiss, and swipe-isolate need **no changes at all**. This is why
per-node naming was worth the pipeline work.

---

## 5. Regrounding the AI insight

**Change this before pointing the viewer at real scans.**

Current behaviour: capture a framebuffer screenshot, send to a vision model, receive freeform
clinical text. On a generic atlas that is a fun demo. On a real patient reconstruction it is a
model making clinical-sounding claims inferred from a picture.

**New behaviour: send measurements, not pixels.**

```ts
const context = {
  structure: "liver",
  label: "Liver",
  volume_ml: 1486.2,
  hu_mean: 54.1,
  hu_std: 12.8,
  study: { modality: "CT", body_part: "ABDOMEN", voxel_spacing_mm: [0.7, 0.7, 0.7] },
  reference: ANATOMY_DB["liver"],
};
```

The model explains and contextualises figures the pipeline measured. It does not infer
findings from a rendering.

**Prompt constraints:**
- Describe what the measurements indicate, referencing typical ranges
- State explicitly that this is visualisation support, not diagnosis
- Never assert a finding not supported by a supplied number
- Do not speculate about pathology

Label the panel **"MEASUREMENTS & CONTEXT"** rather than "MED-AI INSIGHT". The rename is not
cosmetic — it is the difference between a tool that reports and a tool that appears to
diagnose.

The vision path can stay behind a flag for the bundled demo study. It must be off for pipeline
data.

---

## 6. Serving

New endpoints on S12:

```
GET /studies                         list: id, modality, body_part, date, thumbnail
GET /studies/{id}/structures         structures.json
GET /studies/{id}/mesh/{group}.glb   presigned redirect, gold only
GET /studies/{id}/lineage            existing, surfaced in the HUD
GET /contracts/mesh_contract/1.0     published contract
```

CORS for the viewer origin. Presigned URLs with short TTL — never serve object storage
directly.

---

## 7. Build order

Do not start at the viewer. The contract has to exist before anything can consume it.

| Step | Work |
|---|---|
| 1 | S8 structure mode on one study, one group. Verify labels and masks are sane |
| 2 | S8b group map; per-structure measurements computed and stored |
| 3 | S9/S10 per-structure meshing with budgets. Check total payload size early |
| 4 | S11 multi-node GLB export + `structures.json`. **Open a GLB in a glTF viewer and confirm node names survive export** |
| 5 | S12 endpoints + contract publication |
| 6 | Viewer: contract validation, dynamic layers, manifest-driven anatomy data |
| 7 | Viewer: study picker, real vitals, provenance line |
| 8 | AI regrounding |
| 9 | Proof: load a head study, confirm defacing applied and the mesh has no face |

Step 4 is the one most likely to surprise you. Exporters sometimes rename or flatten nodes,
and the entire integration depends on names surviving. Verify it before building step 6.

---

## 8. What not to do

- **Do not load meshes from silver or bronze.** Gold only, same rule as everything else.
- **Do not skip the defacing check in the viewer** because the pipeline already does it. The
  viewer is where a face becomes visible.
- **Do not hardcode the eight original system names.** Groups are per-study.
- **Do not send patient reconstructions to a vision model.** See §5.
- **Do not implement viewer roadmap items** — voice commands, AR mode, quiz mode, unassigned
  swipe actions. Out of scope.

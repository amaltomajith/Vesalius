# Vesalius — Build Kickoff

## Setup

```bash
mkdir vesalius && cd vesalius
git init

cp /path/to/CLAUDE.md .
cp -r /path/to/refs .
ln -s CLAUDE.md AGENTS.md

# viewer goes in as a subdirectory, not a rewrite
git subtree add --prefix=viewer <holoscan-repo-url> main --squash
# or simply: cp -r /path/to/Holoscan ./viewer
```

Repo shape:

```
vesalius/
  CLAUDE.md
  refs/
  pipeline/        <- new, Python
  viewer/          <- existing Holoscan app
  docker-compose.yml
```

In Antigravity, use plan mode for every prompt below. Review the plan before approving.

---

## Phase A — Pipeline

### A1 · Foundation

```
Read CLAUDE.md and every file in refs/ before responding.

Plan S1 and S2 only — ingest and parse/index. Do not plan beyond S2.

Cover:
- Repo layout under pipeline/ so later stages have a home
- Docker Compose: Postgres, MinIO, API
- Catalog schema from refs/DATA-MODEL.md as migrations, including the
  restricted/public split and the role grants enforcing it
- S1: archive upload, DICM magic validation, SHA-256 content addressing,
  bronze storage, duplicate detection
- S2: metadata extraction with stop_before_pixels, instance/series/study
  grouping, catalog population
- The transform_runs idempotency mechanism
- Tests with fixtures from pydicom test data

Call out any place refs/ is ambiguous, and any decision you are making that
the refs do not specify. Do not write code yet.
```

Then: `Implement the approved plan. ruff and mypy clean, tests alongside. Stop after S2.`

### A2 · Quality gates

```
Plan S3. refs/TESTING.md Part 1 has the full check list with reason codes
and thresholds.

Every reason code needs a test that triggers it, with a fixture generated
programmatically from clean data — do not hand-edit binaries. Quarantine
carries the failing values, not just a code.
```

### A3 · De-identification

```
Plan S4 and S5 together. refs/DICOM.md Part 1 is the authority.

S5 must be independent code from S4 — a de-identifier that verifies itself
proves nothing.

Include the PHI injection harness from refs/TESTING.md Proof 1. I want the
removal rate as a measured number across all three channels.
```

### A4 · Volume assembly

```
Plan S6. refs/DICOM.md Part 2 is the authority.

This is the stage most likely to be silently wrong. Specific attention to
slice ordering by position projection, the three different spacing tags, HU
rescaling with signed/unsigned handling, and building the affine from
ImageOrientationPatient rather than assuming identity.

Include a test that would catch a mirrored or axis-swapped volume.
```

### A5 · Defacing gate

```
Plan S7 and the gate it places on S9. refs/GEOMETRY.md has the body-part
trigger list and both approaches.

The gate fails closed: surface extraction refuses when body part is
head-adjacent and no successful defacing run exists. Record a transform_run
even when defacing is not required, with the reason.
```

---

## Phase B — Structures and meshing

### B1 · Segmentation

```
Plan S8 structure mode and S8b group mapping. refs/INTEGRATION.md sections
2 covers both.

First task: verify TotalSegmentator licensing for code and model weights
separately, and report before building on it. If it does not clear, propose
a fallback from the list in that section.

Per-structure measurements (volume_ml, hu_mean, hu_std, bbox) are computed
here from the mask and volume, before meshing.
```

### B2 · Per-structure meshing and export

```
Plan S9, S10, S11 for structure mode. refs/INTEGRATION.md section 2 and
refs/GEOMETRY.md.

Per-structure marching cubes, per-group face budgets with proportional
allocation and a floor for small structures, multi-node GLB per group,
structures.json manifest.

Before implementing anything downstream: export one GLB and open it in a
glTF viewer. Confirm node names survive export unmodified. Report what you
see. The entire integration depends on this.
```

### B3 · Serving

```
Plan S12 including the new mesh endpoints in refs/INTEGRATION.md section 6,
the recursive lineage query from refs/DATA-MODEL.md, and publication of both
volume_contract and mesh_contract.

Artifacts serve from gold only, presigned, short TTL.
```

---

## Phase C — Viewer

> Read `refs/VIEWER-CONSTRAINTS.md` before touching viewer code. Several
> things there look removable and are not.

### C1 · Contract and dynamic layers

```
Plan viewer changes for contract validation and dynamic layers.
refs/INTEGRATION.md sections 3 and 4, refs/VIEWER-CONSTRAINTS.md for what
must not break.

- validateMeshContract on load, including the deface check
- layerStore: eight hardcoded booleans -> groups from the manifest
- LayerPanel iterates groups
- SystemLayer takes a URL prop instead of a static path
- Per-group loading states; dispose previous study's materials on switch

Do not change the gesture engine. Do not retune its constants.
```

### C2 · Manifest-driven anatomy and study picker

```
Plan the anatomy data inversion and the study picker.

anatomyDatabase becomes manifest-first: manifest supplies name, label and
measurements; the static database optionally enriches, keyed on segmentation
labels; the prettifying fallback stays.

Study picker calls GET /studies. Keep a bundled demo study so the viewer
runs without a pipeline instance.

Vitals panel: simulated values -> real study metadata. Add the provenance
line (pipeline version, series id).
```

### C3 · AI regrounding

```
Plan the AI insight change per refs/INTEGRATION.md section 5.

Send measurements, not pixels. The model contextualises figures the pipeline
measured; it does not infer findings from a rendering. Rename the panel to
MEASUREMENTS & CONTEXT.

Preserve the activeRequestRef race guard.

Keep the vision path behind a flag for the bundled demo study only. It must
be off for pipeline data.
```

---

## Phase D — Proofs

```
Implement the four proofs in refs/TESTING.md Part 2 as tests/proofs/:
PHI injection, defacing gate, reproducibility, contract violation.

For reproducibility, expect the first run to fail — hidden state is normal.
Find it rather than weakening the assertion, and tell me what it was.

Add a fifth: load a head study end to end in the viewer and confirm the
rendered mesh has no facial surface.
```

---

## Notes

- **Plan mode every time.** Reviewing a plan is far cheaper than reviewing a
  wrong implementation.
- **Stop-after instructions matter.** Without them the agent runs ahead and
  you lose review granularity.
- **The refs are the authority.** If the agent contradicts one, say so and
  point at the file.
- **B2 is the risk point.** If node names do not survive GLB export, Phase C
  cannot work as specified. Verify before building on it.
- **Do not let the agent implement viewer roadmap items** — voice commands,
  AR mode, quiz mode, unassigned swipe actions.

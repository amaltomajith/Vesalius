# CLAUDE.md — Phantom

Project context for AI coding agents. Read this first, every session.

**Other agents:** `ln -s CLAUDE.md AGENTS.md`

---

## Reference documents

Do not implement from memory. Consult the relevant ref before writing code.

| File | Read before |
|---|---|
| `refs/STAGES.md` | Any pipeline stage — I/O contracts, failure modes, idempotency |
| `refs/DICOM.md` | De-identification (S4/S5) or volume assembly (S6). **Highest-risk stages** |
| `refs/GEOMETRY.md` | Segmentation, defacing, meshing, GLB export (S7-S11) |
| `refs/DATA-MODEL.md` | Schema, lineage, contracts |
| `refs/TESTING.md` | Quality gates, the four proofs, fixtures |
| `refs/INTEGRATION.md` | Anything connecting pipeline to viewer |
| `refs/VIEWER-CONSTRAINTS.md` | Any change to the viewer codebase |

If a ref contradicts this file, **the ref wins** — it is more specific. If refs conflict with
each other or are silent, ask rather than guess.

---

## What this is

**Phantom** turns identifiable patient scans into 3D data that is safe to use, and proves it.

Two systems, one platform:

| System | Role |
|---|---|
| **Pipeline** (Python/FastAPI) | Ingests DICOM, validates, de-identifies across three channels, produces governed volumes and meshes with lineage |
| **Viewer** (React/Three.js) | Gesture-controlled 3D hologram interface. Consumes pipeline output through a published contract |

The pipeline is the deliverable. **Subject: data engineering.** The viewer is what makes the
output legible — not the centrepiece.

### Thesis

> De-identifying medical imaging metadata is not the same as de-identifying medical imaging
> output.

A surface mesh from a head CT is a recognisable face. The pipeline *creates* a
re-identification channel its input only latently contained. Engineering against that is the
point of the project — and the viewer makes it concrete, because there the face is on screen
next to a study identifier.

---

## Architecture

**Three zones:**

| Zone | Contents | Rules |
|---|---|---|
| `bronze/` | Raw DICOM as received | Immutable, content-addressed, **PHI-bearing, never served** |
| `silver/` | De-identified validated volumes | Immutable per pipeline version |
| `gold/` | GLB meshes, NIfTI, manifests | Disposable, rebuildable, servable |

**Twelve stages, S1-S12.** See `refs/STAGES.md`. Two hard gates:

- **S5 (verify) gates S6** — unverified de-identification never reaches silver
- **S7 (deface) gates S9** — surface extraction refuses on head anatomy without a completed
  defacing run

---

## Hard rules

1. **Never use real patient DICOM.** Public sources only (TCIA, 3D Slicer samples, pydicom
   test data). If asked to process a real clinical study, refuse and explain.
2. **Never write to bronze after ingest.** Write-once.
3. **Never serve from bronze or silver.** Gold only. This includes meshes.
4. **Fail closed on the defacing gate** — in the pipeline *and* in the viewer. Refuse, do not
   warn.
5. **Quarantine, never drop.** Rejected series get a reason code and a row.
6. **Never auto-repair bad geometry.** Do not interpolate missing slices or force uniform
   spacing. Plausible wrong geometry is worse than an honest failure.
7. **Every stage records a `transform_run`**, including skips and failures, with parameters.
8. **Never send patient reconstructions to a vision model.** See `refs/INTEGRATION.md` §5.
9. **Never weaken a test to make it pass.** If reproducibility fails, find the hidden state.
10. **Original UIDs, patient IDs and date offsets live only in the `restricted` schema.** The
    serving role has no grant on it.

---

## The traps

Each produces output that looks correct and is not.

**Slice ordering.** Sorting by `InstanceNumber` or `SliceLocation` appears to work and
produces geometrically wrong volumes. Sort by projecting `ImagePositionPatient` onto the slice
normal. → `refs/DICOM.md`

**Spacing.** `SliceThickness` is not slice spacing. Derive from consecutive positions.
`PixelSpacing` is `[row, column]` = `[y, x]`. → `refs/DICOM.md`

**Axis conventions.** DICOM is LPS/mm, NIfTI is RAS, glTF is Y-up/metres. Skipping conversion
gives a model on its side at 1000x scale — or a silently mirrored volume. → `refs/GEOMETRY.md`

**Node names surviving export.** The viewer keys entirely on GLB node names matching
segmentation labels. Some exporters rename or flatten nodes. Verify in a glTF viewer before
building anything on top. → `refs/INTEGRATION.md` §7

**Viewer settings that look like cruft.** `depthWrite: false`, `scene.clone(true)`,
per-mesh materials, the uuid/name split. All load-bearing. →
`refs/VIEWER-CONSTRAINTS.md`

---

## Stack

**Pipeline:** FastAPI · Pydantic · Dagster (fall back to Celery+Redis if it costs too much
time) · PostgreSQL · MinIO · pydicom · SimpleITK · NumPy · scikit-image · trimesh · fast-simplification ·
pytesseract · Docker Compose

**Viewer:** React 19 · Vite 8 · TypeScript 6 · Three.js · React Three Fiber · Drei ·
MediaPipe tasks-vision · Zustand

All open source. Do not introduce a paid service.

**Library notes:** `pydicom` for de-identification — full tag control required. `SimpleITK`
for resampling and NIfTI — do not hand-roll. Do not use SimpleITK's series reader for S4; it
abstracts away the tags that stage must inspect.

---

## Conventions

- Python 3.11+, type hints throughout, Pydantic at every boundary
- `ruff` and `mypy` clean before commit
- TypeScript strict, no `any`
- Stages are pure where possible: `(input, params) -> output`, side effects at the edges
- Parameters affecting output are **versioned and recorded**, never inline literals
- Structured logging. **Never log values from the `restricted` schema**
- Fail loudly on data correctness, degrade gracefully on infrastructure

---

## Determinism

The reproducibility proof requires byte-identical rebuilds. Watch for: timestamps in file
headers (fix to a constant epoch), JSON key ordering (`sort_keys=True`), mesh library thread
scheduling (pin single-threaded in tests), unseeded randomness (seed from the parameter set).

---

## Current state

Pipeline: not started. Viewer: exists, loads eight static GLB files of a generic body.

**Build order — governance first, geometry second, viewer last:**

```
S1-S2   ingest, index
S3      quality gates
S4-S5   de-identify, verify
S6      volume assembly
S7      deface
S8-S11  segment, mesh, export
S12     serving
        integration: contract -> viewer  (refs/INTEGRATION.md §7)
        proofs
```

Do not skip ahead to mesh stages or viewer work. The governance spine is the project.

---

## Working with me

- **Small reviewable increments.** One stage at a time, with tests.
- **State assumptions before acting.** If the refs do not cover it, ask.
- **Tests alongside implementation**, never after.
- **Never fabricate a result.** If a test did not run, say so. If a proof fails, report the
  failure — a documented gap beats a false claim.
- **Do not implement viewer roadmap items.** Voice commands, AR mode, quiz mode, unassigned
  swipe actions are all out of scope.

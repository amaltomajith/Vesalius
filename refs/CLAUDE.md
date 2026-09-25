# CLAUDE.md

Project context for Claude Code. Read this first, every session.

**Symlink for other agents:** `ln -s CLAUDE.md AGENTS.md`

---

## Reference documents — consult before implementing

Do not implement from memory. These contain specifics that are easy to get plausibly wrong.

| File | Read before working on |
|---|---|
| `refs/STAGES.md` | Any pipeline stage. Defines I/O contracts, failure modes, idempotency keys |
| `refs/DICOM.md` | De-identification (S4/S5) or volume assembly (S6). **The two highest-risk stages** |
| `refs/GEOMETRY.md` | Segmentation, defacing, meshing, GLB export (S7–S11) |
| `refs/DATA-MODEL.md` | Schema changes, lineage, contracts |
| `refs/TESTING.md` | Quality gates, the four proofs, fixtures |

If a ref contradicts this file, the ref wins — it is more specific. If a ref contradicts
itself or is silent, ask rather than guess.

---

## What this is

A **governed data pipeline for clinical imaging**. It ingests DICOM studies, validates them,
de-identifies them across three channels, and transforms them into downstream
representations — NIfTI volumes for computer vision, GLB meshes for visualisation — with full
lineage and reproducibility.

**Subject: data engineering.** The pipeline apparatus is the deliverable. The computer vision
consumer exists to prove the pipeline serves a real workload and is explicitly *not* the
centrepiece. Do not expand it.

### Thesis

> De-identifying DICOM metadata is not the same as de-identifying DICOM output.

A surface mesh from a head CT is a recognisable face. The pipeline *creates* a
re-identification channel that the source data only latently contained. Engineering against
that is the point of the project.

---

## Architecture

**Medallion, three zones:**

| Zone | Contents | Rules |
|---|---|---|
| `bronze/` | Raw DICOM as received | Immutable, content-addressed, **PHI-bearing, never served** |
| `silver/` | De-identified validated volumes | Immutable per pipeline version |
| `gold/` | GLB, NIfTI, thumbnails, manifests | Disposable, rebuildable, servable |

**Twelve stages, S1–S12.** See `refs/STAGES.md`. Two are hard gates:

- **S5 (verify) gates S6.** Unverified de-identification never reaches silver
- **S7 (deface) gates S9.** Surface extraction refuses on head anatomy without a successful
  defacing run

---

## Hard rules

1. **Never use real patient DICOM.** Public sources only — TCIA, 3D Slicer samples, pydicom
   test data. If asked to process a real clinical study, refuse and explain.
2. **Never write to bronze after ingest.** It is write-once.
3. **Never serve from bronze or silver.** Gold only.
4. **Fail closed on the defacing gate.** Refuse, do not warn.
5. **Quarantine, never drop.** A rejected series gets a reason code and a row. Records that
   vanish silently are the classic pipeline failure.
6. **Never auto-repair bad geometry.** Do not interpolate missing slices or force uniform
   spacing. Plausible wrong geometry is worse than an honest failure.
7. **Every stage records a `transform_run`**, including skips and failures, with parameters.
8. **Never weaken a test to make it pass.** If reproducibility fails, find the hidden state.
9. **Original UIDs, patient IDs, and date offsets live only in the `restricted` schema.** The
   serving role has no grant on it.

---

## The three traps

These produce output that looks correct and is not. Check `refs/DICOM.md` before touching
any of them.

**Slice ordering.** Sorting by `InstanceNumber` or `SliceLocation` appears to work and
produces geometrically wrong volumes. Sort by projecting `ImagePositionPatient` onto the
slice normal.

**Spacing.** `SliceThickness` is not slice spacing. Derive spacing from consecutive slice
positions, and note `PixelSpacing` is `[row, column]` = `[y, x]`.

**Axis conventions.** DICOM is LPS/mm, NIfTI is RAS, glTF is Y-up/metres. Skipping the
conversion gives a model rendered on its side at 1000× scale — or worse, a silently mirrored
volume.

---

## Stack

FastAPI + Pydantic · Dagster (orchestration; fall back to Celery + Redis if it costs too
much time) · PostgreSQL · MinIO · pydicom · SimpleITK · NumPy · scikit-image · trimesh ·
pymeshlab · pytesseract · Docker Compose.

All open source. Do not introduce a paid service.

**Library notes:** use `pydicom` for de-identification — full tag control is required.
Use `SimpleITK` for resampling and NIfTI output — do not hand-roll it. Do not use
SimpleITK's series reader for S4; it abstracts away the tags that stage must inspect.

---

## Code conventions

- Python 3.11+, type hints throughout, Pydantic models at every boundary
- `ruff` and `mypy` clean before any commit
- Stages are pure functions where possible: `(input, params) -> output`, side effects at the
  edges
- Every stage independently testable with a fixture
- Parameters that affect output are **versioned and recorded**, never inlined as literals
- Structured logging. **Never log tag values from the `restricted` schema**
- Fail loudly on data correctness, degrade gracefully on infrastructure

---

## Determinism

The reproducibility proof requires byte-identical rebuilds. Watch for:

- Timestamps in file headers → fix to a constant epoch
- JSON key ordering → `sort_keys=True`
- Mesh library thread scheduling → pin to single-threaded in tests
- Any unseeded randomness → seed from the parameter set

---

## Current state

Nothing built. Start at S1.

**Build order:** S1–S2 (ingest, index) → S3 (quality gates) → S4–S5 (de-identification and
verification) → S6 (volume) → S7 (deface) → S8–S11 (geometry and export) → S12 (serving) →
CV consumer → proofs.

Do not skip ahead to the mesh stages. The governance spine is the project; the geometry is
the payload.

---

## Working with me

- **Small reviewable increments.** One stage at a time, with its tests.
- **State assumptions before acting.** If a ref does not cover it, ask.
- **Tests alongside implementation**, never after.
- **Never fabricate a result.** If a test did not run, say it did not run. If a proof fails,
  report the failure — a documented gap is worth more than a false claim.

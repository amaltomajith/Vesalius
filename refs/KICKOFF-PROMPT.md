# Claude Code Kickoff

## Setup

```bash
mkdir medical-imaging-pipeline && cd medical-imaging-pipeline
git init

# copy CLAUDE.md to the project root and refs/ alongside it
cp /path/to/CLAUDE.md .
cp -r /path/to/refs .
ln -s CLAUDE.md AGENTS.md

claude
```

Then press **Shift+Tab twice** to enter plan mode before sending the prompt below.
Plan mode makes Claude research and propose before writing anything.

---

## Prompt 1 — Planning

```
Read CLAUDE.md and every file in refs/ before responding.

This is a data engineering project: a governed pipeline that ingests DICOM,
de-identifies it across three channels, and produces NIfTI volumes and GLB
meshes with full lineage and reproducibility.

Plan the implementation of stages S1 and S2 only — ingest and parse/index.
Do not plan beyond S2.

Your plan must cover:
- Repository layout for the full project, so later stages have a home
- Docker Compose: Postgres, MinIO, and the API
- The catalog schema from refs/DATA-MODEL.md, as migrations, including the
  restricted/public schema split and the role grants that enforce it
- S1: archive upload, DICM magic validation, SHA-256 content addressing,
  bronze storage, duplicate detection
- S2: metadata extraction with stop_before_pixels, instance/series/study
  grouping, catalog population
- The transform_runs idempotency mechanism, since every later stage depends
  on it
- Tests for both stages, including fixtures from pydicom test data

Call out explicitly:
- Any place where refs/ is ambiguous or silent
- Any decision you are making that the refs do not specify
- What you would defer to a later stage and why

Do not write code yet.
```

---

## Prompt 2 — After approving the plan

```
Implement the approved plan for S1 and S2.

Constraints:
- ruff and mypy clean
- Tests alongside implementation, not after
- Every stage records a transform_run, including failures
- Nothing from the restricted schema reaches logs

Stop after S2 and show me: the schema migration, the two stage
implementations, and the passing test output. Do not start S3.
```

---

## Subsequent stages

One prompt per stage group. Always plan mode first.

**S3 — quality gates**
```
Plan S3, the quality gate stage. refs/TESTING.md Part 1 has the full check
list with reason codes and thresholds. Every reason code needs a test that
triggers it, and a fixture generated programmatically from clean data.
Quarantine must carry the failing values, not just a code.
```

**S4/S5 — de-identification and verification**
```
Plan S4 and S5 together. refs/DICOM.md Part 1 is the authority on the tag
profile, deterministic UID remapping, date shifting, private tags, and
burned-in pixel PHI.

S5 must be independent code from S4 — a de-identifier that verifies itself
proves nothing.

Also plan the PHI injection harness from refs/TESTING.md Proof 1. I want the
removal rate as a measured number across all three channels.
```

**S6 — volume assembly**
```
Plan S6. refs/DICOM.md Part 2 is the authority.

This stage is the most likely in the whole pipeline to be silently wrong.
Pay specific attention to slice ordering by position projection, the three
different spacing tags, HU rescaling with signed/unsigned handling, and
building the affine from ImageOrientationPatient rather than assuming
identity.

Include a test that would catch a mirrored or axis-swapped volume.
```

**S7 — defacing**
```
Plan S7, the defacing stage, and the gate it places on S9.

refs/GEOMETRY.md has the body part trigger list and both approaches. The
gate must fail closed: surface extraction refuses when body part is
head-adjacent and no successful defacing run exists.

Record a transform_run even when defacing is not required, with the reason.
Absence of defacing must always be explained.
```

**S8–S11 — geometry and export**
```
Plan S8 through S11. refs/GEOMETRY.md is the authority on thresholds,
marching cubes parameters, mesh post-processing order, and the LPS to glTF
axis conversion.

Mesh post-processing order matters: largest component, clean degenerates,
Taubin smoothing, quadric decimation, normals last.

Record input and output face counts.
```

**S12 — serving**
```
Plan S12. Endpoints are listed in the project plan. Include the recursive
lineage query from refs/DATA-MODEL.md and the published contract endpoint.
Artifacts serve from gold only.
```

**Proofs**
```
Implement the four proofs in refs/TESTING.md Part 2 as a tests/proofs/
suite: PHI injection, defacing gate, reproducibility, contract violation.

For reproducibility, expect the first run to fail — hidden state is normal.
Find it rather than weakening the assertion, and tell me what it was.
```

---

## Notes

- **Plan mode every time.** Shift+Tab twice. Reviewing a plan is much cheaper
  than reviewing a wrong implementation.
- **Stop-after instructions matter.** Without them the agent runs ahead and
  you lose review granularity.
- **The refs are the authority.** If the agent contradicts one, say so and
  point at the file.
- **Build order is governance first, geometry second.** The mesh output is
  the fun part and the least of what is graded.

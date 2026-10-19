# CLAUDE.md — Vesalius (pipeline + viewer)

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

**Vesalius** turns identifiable patient scans into 3D data that is safe to use, and proves it.

_Named Phantom until 2026-10-19 (env vars were `PHANTOM_*`, now `VESALIUS_*`). Lowercase
"phantom" in the code is the imaging sense, a synthetic test object (`tests/fixtures/head_phantom.py`,
`pipeline.demo.phantoms`), and stays; so does the `phantom_viewer_nm` Docker volume name._

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

**Phase one (built and verified):** pipeline S1-S12, the CV consumer, the four proofs, the
point-and-click console (`/console`) and the landing page (`/`, a real openly licensed head CT
run through the pipeline, face removed at S7).

**Phase three (built): one product.** The landing page stays at `/`; everything else is ONE React
app at `/app` (source `viewer/`): dashboard, Run a scan (the old console, ported), Studies, the
hands-controlled hologram (`/app/hologram/:id`, fullscreen), the full-body atlas (`/app/body`,
Holoscan's 8 systems at full detail from `assets/atlas/`, parts under non-commercial or unstated
licences removed, see `assets/atlas/EXCLUDED.md`) and Proofs. `/console` and `/viewer` redirect.
Optional AI (`src/api/routers/ai.py`) is off without `GROQ_API_KEY`; scans send measurements only.
Look: surgical green everywhere (`refs/VIEWER-CONSTRAINTS.md` design tokens updated). Since
2026-10-18 the app is a sage frame (top bar, `--frame*` tokens; the black frame read as too much
contrast) around soft white cards. Dashboard, Studies and Proofs share the card language; their
numbers come from the structures manifests (`viewer/src/lib/stats.ts` via `useStudyStats`,
validated before counting; chart palette checked with the dataviz validator, order fixed in
`SYSTEMS`). Run a scan keeps its layout on one white sheet. The host has no `viewer/node_modules`: run vitest/tsc/build in
`node:22-slim` with the repo mounted and a `phantom_viewer_nm` volume on `node_modules`.

**Phase two (built): pipeline -> viewer integration**, per `refs/INTEGRATION.md`:

```
M0  refs, rules, licences                     (LICENSES.md)
M1  public CT series (chest, abdomen, head)    TCIA CC BY / Slicer samples, never committed
M2  S8 structure mode + S8b groups, per-structure S9-S11, structures.json
M3  S12 study endpoints + mesh_contract v1.0
M4  viewer/ (Holoscan) at /viewer: contract validation, dynamic layers, study picker
M5  landing: full-body atlas -> the real scan
M6  proofs (incl. proof 5: head study in the viewer has no face)
```

M0-M6 are built. Proof 5 is `tests/proofs/test_head_study_has_no_face.py` (a head study read
back only through the viewer's `/studies` routes has no vertex in S7's face region; teeth: the
uncut study does), runnable from the console as proof "viewer"; the viewer-side refusal is in
`viewer/src/contract/meshContract.test.ts`.

The landing page (redesigned 2026-10-17, dark by user decision) pitches the product, not the
pipeline: a CT becomes a labelled 3D body you steer with your hands; de-identification is one
short beat. `static/hall-scene.js` is one scroll-scrubbed three.js hall (procedural columns and
arches, so the first frame needs no download) with an anatomy statue (`static/body/`, built
deterministically by `scripts/build_landing_body.py`, aligned to the CT by cranial breadth; the
page itself no longer loads the CT, `static/scan/` stays as that build's reference). Beats
(`CHAPTERS`), all on the statue: scan (the gantry ring reads the marble into slices that part),
privacy (identity tags pinned to the head strike through, the S7 sheet sears the face, the slices
close up, visor), AI labels (marble dissolves, systems light in turn, labels in lanes), hands
(MediaPipe 21-point hand acting the viewer's gestures). Below it, `.vow` is a second pinned stage
(glow floods, sans blurs while the italic stays sharp, proof tickets fly in). `window.__hold = t`
and `window.__holdVow = p` pin either one for inspection; `playwright-cli` screenshots are the
reliable way to check them here.

Viewer facts worth knowing before touching it:

- Build: `cd viewer && npm ci && npm run build` (prebuild vendors MediaPipe), tests `npm test`
  (vitest). The api image builds it in a Node stage and serves it at `/viewer/`.
- Demo study: `python scripts/bundle_viewer_demo.py --api <url>` pulls a served chest study into
  `viewer/public/demo/` (gitignored, like every public scan).
- GEOMETRY.md exports Posterior -> +Z, so the viewer turns every study half a turn about the
  vertical axis to face the camera. A rotation, never a mirror.
- The AI vision path is removed, not flagged off: MEASUREMENTS & CONTEXT is computed locally from
  the manifest, so no frame or study data ever leaves the browser.
- Gestures v3.3 (2026-10-18, user request): pinch/fist navigation, point and hold 2 s to select,
  an armed open palm of either hand (held still 0.4 s, any angle, picked by behaviour not
  MediaPipe's labels) sweeps the rest away sideways (it follows the hand; leaving the camera
  through the side commits); never during or just after a zoom or drag; in a crowd only the
  nearest person's hands are followed (`hooks/operatorLock.ts`, 2 hands or fewer pass as before); two index fingers together = AI snapshot
  (atlas only); an animated 3D hand guide. Pure engines in `viewer/src/hooks/gestureEngine.ts` and
  `selectionEngine.ts`, regression-tested on landmark sequences (`handsPipeline.test.ts`),
  documented in `refs/VIEWER-CONSTRAINTS.md`.
  Hands and mouse share one click model (select, again to isolate, empty space to step back).
- The 3D room (2026-10-18, user request "like the website") is the landing's hall: green-black
  with a spotlight, a pedestal and orbit ring (`HallStage.tsx`), one left panel, the part card
  with the name in Playfair italic, and a validated anatomical palette (`layerStore.GROUP_COLOURS`).
  Its look lives in `styles/room.css`; details in `refs/VIEWER-CONSTRAINTS.md` design tokens.
- Holoscan's pre-existing eslint errors are left as they were (minimal edits, VIEWER-CONSTRAINTS).

**Deployment (built, not yet provisioned):** `DEPLOY.md`, nothing needing a card (research
2026-10-18; the user has no card, 2026-10-19): one arm64 VM on **Azure for Students** ($100 a
year, disabled not billed when spent), segmenting on `B4ps_v2` (16 GB) then serving on
`B2pls_v2` (4 GB; the stack idles at ~0.6 GB). Oracle's Always Free A1 (2 OCPU / 12 GB since
15 June 2026, needs a card) is the documented alternative. DuckDNS + Caddy for HTTPS; the arm64
api image built on GitHub's free arm64 runners into ghcr.io (github.com/amaltomajith/Vesalius);
MinIO built from its final upstream source (`docker/minio/`); UptimeRobot. `VESALIUS_PUBLIC=1` makes
every mutating route admin-only (`src/api/auth.py`, scrypt hash + signed cookie; a unit test fails
if a new mutating route lacks `require_admin`). The deliberate exception is the AI explanations,
open to visitors but rate-limited per IP and per day. `docker-compose.prod.yml` + `deploy/Caddyfile`:
Caddy is the only host port; `files.<domain>` serves GET/HEAD `/gold/*` only. The ARM64 target is
why S10 decimates with fast-simplification (S10 params v2).

**Layout (deliberate deviation from refs/KICKOFF.md):** the pipeline stays at the repo root
(`src/`, `tests/`); the viewer lives in `viewer/`. Moving the pipeline into `pipeline/` would
churn every import for no behavioural gain.

**Licences:** see `LICENSES.md` before shipping any third-party data or model. The anatomy
atlas contains non-commercial sub-models that must never be shipped.

---

## Working with me

- **Small reviewable increments.** One stage at a time, with tests.
- **State assumptions before acting.** If the refs do not cover it, ask.
- **Tests alongside implementation**, never after.
- **Never fabricate a result.** If a test did not run, say so. If a proof fails, report the
  failure — a documented gap beats a false claim.
- **Do not implement viewer roadmap items.** Voice commands, AR mode, quiz mode, unassigned
  swipe actions are all out of scope.

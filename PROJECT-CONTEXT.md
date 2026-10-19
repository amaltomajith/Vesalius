# Vesalius: full project context

This is a self-contained briefing for anyone, human or AI, who has **not** seen the repository or
the conversations that built it. It covers what the project is, why it exists, how every part
works, the decisions made and their reasons, the current state, and what remains.

*As of 2026-10-08. Branch `phase-one-three` (4 commits: `8acc79e` → `04832c3` → `78cbabf` → `6f51cb3`).*

---

## 1. TL;DR

- **Vesalius** is a governed data pipeline that turns CT and MR scans (DICOM) into 3D data that is
  safe to share, and **proves** it with automated tests.
- **Thesis:** *de-identifying the metadata of a medical image is not the same as de-identifying
  its output.* A 3D surface mesh built from a head CT is a recognisable face, so the pipeline
  itself *creates* a re-identification channel. Engineering against that, and proving the
  engineering works, is the point.
- **Two systems, one product:**
  - a Python/FastAPI **pipeline** of 12 stages (S1–S12) over a bronze/silver/gold lakehouse in
    Postgres and MinIO;
  - a React/Three.js **app** (`/app`) where you run scans, watch every stage, explore results as a
    full-screen hologram you steer with your hands (MediaPipe), and browse a full-body anatomy atlas.
- **Subject: data engineering.** The pipeline is the deliverable. The viewer makes the output
  legible: a head study on screen with no face, next to its study identifier.
- **Status:**
  - All 12 stages, the 5 proofs, the app, the landing page and the deployment tooling are built
    and tested.
  - Not yet deployed. The code is ready for a free Oracle Cloud ARM VM; what's left are the
    user's account and server steps.
- **Data:** only public, openly licensed scans (3D Slicer sample data, TCIA CC BY 3.0). Never real
  patient data.

### Repository layout

```
src/
  pipeline/            the pipeline (Python package)
    stages/s1_ingest ... s12_serve   one package per stage
    catalog/           contracts, lineage, structures lookup
    common/            config, errors, HU presets, de-id profile, OCR
    storage/           MinIO clients (one credential per zone)
    demo/              the runner behind the app's "Run a scan" + proofs page
  consumer/            independent CV consumer (numpy + stdlib only)
  api/                 FastAPI app: main.py, auth.py, routers/{catalog,demo,ai,ingest,ui,health}.py,
                       static/ (landing page assets)
viewer/                React app source (served at /app)
assets/atlas/          full-body anatomy atlas GLBs (CC-BY-SA, ~152 MB)
migrations/            Alembic (schemas, roles, grants)
tests/unit|integration|proofs/
scripts/               data fetchers, atlas builders, demo loaders, deploy/ scripts
docker/                api Dockerfile (Node build stage + Python), minio-init
deploy/Caddyfile       production reverse proxy
docker-compose.yml     local stack;  docker-compose.prod.yml   production overlay
refs/                  design references (STAGES, DICOM, GEOMETRY, DATA-MODEL, TESTING,
                       INTEGRATION, VIEWER-CONSTRAINTS)
CLAUDE.md              rules and context for coding agents;  DEPLOY.md   deployment guide
```

---

## 2. Goals and audience

- **A portfolio-grade data-engineering project.** It shows ingestion, validation, quarantine,
  de-identification, lineage, versioned parameters, data contracts, reproducibility and access
  control on a domain where mistakes look correct.
- **Three ways it is used:**
  1. **Local demo:** anyone with Docker runs it on their machine, with no login and loopback only.
  2. **Public showcase (planned):** read-only for visitors.
  3. **Admin:** one login on the public deployment, which unlocks uploads, pipeline runs, proofs
     and the optional AI.

---

## 3. Hard rules (non-negotiable)

1. **Never use real patient DICOM.** Public sources only. Refuse to process a real clinical study.
2. **Never write to bronze after ingest.** It is write-once.
3. **Never serve from bronze or silver.** Only gold is served, meshes included.
4. **Fail closed on the defacing gate**, in the pipeline *and* the viewer. Refuse; never warn.
5. **Quarantine, never drop.** A rejected series gets a reason code and a row.
6. **Never auto-repair bad geometry.** No interpolating missing slices, no forcing uniform spacing.
   Plausible wrong geometry is worse than an honest failure.
7. **Every stage records a `transform_run`,** including skips and failures, with its parameters.
8. **Never send patient reconstructions to a vision model.**
9. **Never weaken a test to make it pass.** If reproducibility fails, find the hidden state.
10. **Original UIDs, patient IDs and date offsets live only in the `restricted` schema.** The
    serving role has no grant on it.

Also: no paid services (everything is open source; the optional AI is off without a key), and
never log values from `restricted`.

### The traps (output that looks right and is not)

- **Slice ordering:** sorting by `InstanceNumber` or `SliceLocation` "works" and is wrong. Sort by
  projecting `ImagePositionPatient` onto the slice normal.
- **Spacing:** `SliceThickness` is not slice spacing; derive it from consecutive positions.
  `PixelSpacing` is `[row, col]` = `[y, x]`.
- **Axes:** DICOM is LPS in mm, NIfTI is RAS, glTF is Y-up in metres. Skip a conversion and the
  model ends up on its side at 1000× scale, or silently mirrored.
- **GLB node names:** the viewer keys on node names. Exporters and loaders rename them; three.js
  turns spaces into `_` and strips `.[]:/`, and the manifest uses the renamed form.
- **Viewer "cruft" that is load-bearing:** `depthWrite: false`, `scene.clone(true)`, per-mesh
  materials, the uuid/name split.

---

## 4. Architecture

### Zones (medallion lakehouse)

| Zone | Contents | Rules |
|---|---|---|
| `bronze` | Raw DICOM as received | Immutable, content-addressed, PHI-bearing, **never served** |
| `silver` | De-identified, validated volumes (NIfTI) | Immutable per pipeline version |
| `gold` | GLB meshes, NIfTI copies, manifests, thumbnails, reports | Disposable, rebuildable, the only thing served |
| `quarantine` | Rejected series | Reason code plus a row; never dropped |

- **MinIO:** one bucket per zone, each with its **own access key** (`docker/minio-init`). The
  serving code can only presign the gold bucket, because the bucket is not even a parameter.
- **Postgres:** metadata and the catalogue. Key tables: series and instances, `transform_runs`
  (every stage execution with stage version, parameters and input hashes), artifacts,
  `lineage_edges`, `data_contracts` and quarantine. The `restricted` schema holds the
  original→pseudonym maps.
- **Roles:**
  - `pipeline_svc` writes;
  - `serving_ro` is read-only and has no grant on `restricted`;
  - migrations run as the superuser.

### Flow

```
upload/ingest ─► S1..S3 (bronze, validate, quarantine)
              ─► S4 de-identify ─► S5 verify ──gate──► S6 volume (silver, NIfTI RAS)
              ─► S7 deface ──gate──► S8 segment ─► S9 surface ─► S10 post-process ─► S11 export (gold)
              ─► S12 serve (read-only, gold-only, presigned redirects) ─► app / consumers
```

### Hard gates

- **S5 gates S6.** Unverified de-identification never reaches silver.
- **S7 gates S9.** Surface extraction refuses head anatomy without a completed, hash-verified
  defacing run. There is no bypass flag; `skip_deface` cannot skip the gate. A static
  architecture test forbids later stages from importing earlier stages' algorithm or service
  modules; they get the gated volume only through the gate.

---

## 5. The 12 stages

All stages are pure where possible: `(input, params) -> output`, with side effects at the edges.
Every parameter that affects output is versioned (`STAGE_VERSION`, `PARAMS_VERSION`) and recorded
in the `transform_run`. Stage versions are all 1.0.0 except S10, which is 1.1.0.

| # | Stage | What it does | Notes |
|---|---|---|---|
| S1 | Ingest | Receives a zip of DICOM and writes the files to bronze, content-addressed | Write-once. The original filename counts as PHI and is never served |
| S2 | Parse and index | Reads the tags and groups instances into studies and series | |
| S3 | Quality gate | Checks modality, geometry consistency, slice count and spacing | Rejects go to quarantine with a reason code. The idempotency key includes a declared body part |
| S4 | De-identify | Three channels: **tags** (profile-driven, HMAC-keyed UID remap, date shift, patient pseudonym), **private tags**, **burned-in pixels** (OCR via tesseract, then redaction) | pydicom with full tag control. Secrets: `DEID_UID_SECRET`, `DEID_DATE_SHIFT_SECRET` |
| S5 | Verify | Independent re-check of all three channels, including OCR on the output | **Gates S6** |
| S6 | Volume assembly | Sorts slices by position projected on the normal, derives spacing, builds a NIfTI (RAS) with SimpleITK into silver | Never interpolates missing slices. The header is verified by parsing the raw sform bytes |
| S7 | Deface | Finds and removes the face region on head CT, and writes a report with the region in world mm | **Gates S9.** CT only, so head MRI is refused by design |
| S8 | Segment | Mask from Hounsfield-unit presets (`bone`, `bone_dense`, `soft_tissue`, `lung`, `skin_surface`), or **structures mode** | Morphology border effects are a documented risk, not "repaired" |
| S9 | Surface extraction | Masked marching cubes on the gated intensity volume, then the **full affine** index→RAS | `marching_cubes(spacing=)` only scales axes, so the affine is required |
| S10 | Mesh post-process | Largest component → degenerate cleanup → Taubin smoothing → quadric decimation → normals last | See below |
| S11 | Export | Gold artifacts: GLB (RAS→glTF Y-up metres, centred), NIfTI copy, manifest JSON, thumbnail PNG | Fixed keys addressed by series; artifact rows are upserted |
| S12 | Serve | Read-only API over `serving_ro`: catalogue, lineage, reports, `/studies` | Gold only, presigned redirects. It records no `transform_run`: a deliberate exception to rule 7 |

### Structures mode (S8 → S11), the "named structures" preset

- **Segmentation:** S8 runs **TotalSegmentator 2.18** (the `total` task, Apache-2.0 weights, CPU
  torch 2.14). It names over 100 structures (organs, bones, vessels), and its usage telemetry is
  forced off.
- **Groups:** S8b groups the structures into systems (skeletal, cardiovascular, visceral and so
  on, plus vessels and costal cartilages).
- **The face cut:** S8 clears every label inside S7's verified face region (anterior and superior
  of the region's world-mm bounds), so even the facial skeleton is cut. Measured on the real head:
  skull volume went from 711 ml to 598 ml, with a clean anterior plane. If defacing is required and
  the region is missing, S8 fails closed.
- **Meshing:** per-structure meshing with S9 blur `BLUR_SIGMA_MM=1.5` (params v2), because
  TotalSegmentator's fast masks are 3 mm blocks.
- **Budgets:** S10 gives each group a face budget (e.g. skeletal at 120k; floor 800).
- **Export:** S11 writes one GLB per group plus `structures.json`.
- **Measured:**
  - chest: 87 structures in 7 groups, 9.2 MB;
  - abdomen: 64 structures, 9.2 MB;
  - head: defacing required and applied.

### S10 details and decisions

- **Taubin smoothing with `nu = +0.53`.** GEOMETRY.md's own snippet says `nu = -0.53`. Under
  trimesh's real `filter_taubin` that shrinks the mesh continuously (−36% volume over 100
  iterations). The sign was corrected; the volume stays within about 1–2%. A test pins it and a
  mutation flips it back.
- **The decimator is fast-simplification 0.2.0** (quadric edge collapse), swapped in from pymeshlab
  because pymeshlab has no ARM64 (aarch64) wheel and the deployment target is ARM. Measured before
  the swap:
  - it hits the exact target face count;
  - repeated runs are byte-identical;
  - volume stays within 0.3% of pymeshlab's.
  It has no boundary- or normal-preservation option, which is recorded as a residual risk.
  STAGE 1.1.0, PARAMS v2, and the decimator name is recorded in the parameters. Dropping
  pymeshlab also removed three GL system libraries and cut the image by 0.7 GB.
- **Decimation never inflates:** it is skipped when the mesh is already under budget.

---

## 6. Contracts and serving

- **`data_contracts` are immutable** per (name, version). Re-publishing identical content is a
  no-op; different content raises an error, so breaking changes bump the major version.
  - `volume_contract` **v1.0**: NIfTI, RAS, HU, float32, the axis order and units.
  - `mesh_contract` **v1.0**: GLB groups, node names, manifest fields, units and frame.
- **Every S12 data response carries `contract_version`.**
- **`/studies` routes** (list, detail, manifest, structures, `mesh/{group}.glb`, thumbnail):
  - they return **307 redirects to short-lived presigned gold URLs**;
  - the URLs are signed for `MINIO_PUBLIC_ENDPOINT`, because the host is part of the signature;
  - in production that is `files.<domain>` over https (`MINIO_PUBLIC_SECURE=true`).
- **Manifest highlights:**
  - `source_frame: RAS_mm`;
  - `centring_offset_mm`;
  - parameters nested per producing stage;
  - a `deface` block (required/applied, plus `structures_cut`);
  - provenance.
- **Lineage:** `run_stage` writes `lineage_edges` on every success, and the lineage graph is served
  read-only. S1–S5 serve only `*_version` keys, because S1's parameters include the original
  filename.

---

## 7. The five proofs (`tests/proofs/`), plus the CV consumer

Each proof tries to break one promise and has "teeth": a mutation of the guarded behaviour makes
it fail. All five run from the app's Proofs page, inside the api container.

| # | Proof | Claim | Evidence |
|---|---|---|---|
| 1 | PHI injection (`test_phi_injection.py`) | Identifiers are removed across all three channels | 18/18 planted identifiers removed across CT and MR, with positive controls (needs tesseract) |
| 2 | Defacing gate (`test_defacing_gate.py`, `_system.py`) | The pipeline cannot emit an identifying face mesh; the gate fails closed | Real stages plus tampering; a with/without side-by-side PNG |
| 3 | Reproducibility (`test_reproducibility.py`) | Gold is disposable: wipe it, rebuild, byte-identical | In-process, across two processes with different `PYTHONHASHSEED`, and across series |
| 4 | Contract violation (`test_contract_violation*.py`) | Consumers refuse upstream drift, above all a silent left–right mirror | v1.1 (LPS) is published in a rolled-back transaction; the consumer refuses it |
| 5 | Viewer has no face (`test_head_study_has_no_face.py`) | A head study, read only through the viewer's `/studies` routes, has no vertex in S7's face region | It finds the face when the cut is disabled. The viewer-side refusal is in `meshContract.test.ts` |

**The CV consumer** (`src/consumer/`) is a deliberately independent client: numpy and stdlib only,
with its own NIfTI and sform parser and no ITK. It validates against the contract the artifact
declares and refuses:
- LPS, a reflected sform (det < 0, i.e. a mirrored volume), and shear or anisotropy;
- non-finite values;
- a wrong `source_frame`;
- a defacing that was required but not applied;
- a mismatched sha256 or size.

Run it with `python -m consumer <api> <series_id>`.

**Last verification** (2026-09-27, on the new image with the S10 swap):
- S10 integration: 9 passed; structures integration: 4 passed.
- Proofs 2, 3 and 5: 11, 8 and 1 passed.
- The unit suite: 1,158 passed, plus 5 new deploy tests.
- Proofs 1 and 4 don't involve S10 and were not rerun after the swap; CI runs them.

---

## 8. The app (`viewer/` → served at `/app`)

**Stack:** React 19, Vite 8, TypeScript 6 (strict, no `any`), Three.js, React Three Fiber, Drei,
Zustand, react-router 7, MediaPipe tasks-vision (vendored WASM, no CDN at runtime), Phosphor
icons, the Geist font. The api image builds it in a Node stage. FastAPI serves it with an SPA
fallback; `/console` and `/viewer` redirect to it.

| Screen | Route | What it does |
|---|---|---|
| Dashboard | `/app` | What's published, which proofs pass, where to go |
| Run a scan | `/app/pipeline` | Run a built-in sample or upload a public DICOM zip (max 200 MB) with a preset (Bone / Skin surface / Named structures); watch the stages live; see what was removed, the audit trail, and **Enter hologram** |
| Studies | `/app/studies` | Every published study |
| Hologram | `/app/hologram/:id` | Full screen. Each structure is a layer; select one for its measurements; the audit trail; hand gestures or mouse |
| Full body | `/app/body` | The anatomy atlas: 8 systems (skeletal, cardiovascular, visceral, muscular, nervous, lymphoid, joints, regions), with a description per structure |
| Proofs | `/app/proofs` | Run any of the 5 proofs and read the verdict |
| Login | `/app/login` | Admin sign-in (public mode only) |

**Key behaviours:**
- **Contract validation fails closed:** `validateMeshContract` refuses a study whose manifest says
  defacing was required but not applied, so no GLB is even requested.
- **Orientation:** GEOMETRY.md exports Posterior → +Z, so the viewer turns every study half a turn
  about Y to face the camera. It is a rotation, never a mirror.
- **Measurements stay local:** "Measurements & context" is computed in the browser from the
  manifest. The old vision-AI path was **removed**, not merely flagged off.
- **Optional AI** (`src/api/routers/ai.py`, Groq): off unless `GROQ_API_KEY` is set on the server,
  and the key never reaches the browser. Scans send **measurements only**, from an allow-listed
  context; the atlas, which is not a patient, may send a snapshot. In public mode it is admin-only.
- **Selection:** a click path for the mouse (drags ignored) sits beside the gesture path.
- **Design:** "surgical green", a locked light theme. Bone-white `#F3F5F1`, accent `#0F6B52`, deep
  `#0F3B2F`, mint `#C9F2DF`.
- **Atlas:** built from Z-Anatomy (CC-BY-SA 4.0) by `scripts/build_atlas.py`, pure-stdlib glTF
  surgery, about 152 MB.
  - Parts under non-commercial or unstated licences were **removed**: the kidneys and renal
    vessels, the inner ear and ossicles, the telencephalon and all white-matter subtrees.
  - `assets/atlas/EXCLUDED.md` lists them, and a test checks the shipped bytes.
  - About 2,000 structure descriptions are included.

---

## 9. Landing page (`/`)

- **Structure:** a pinned scroll journey with a hero and **5 chapters**:
  1. Received untouched;
  2. Names removed;
  3. The face comes off;
  4. Every structure, named;
  5. Served, and proven.
  A 12-stage ribbon colours the bronze/silver/gold zones and locks at the gates.
- **Visuals:** three.js point clouds (vendored r186). The full-body atlas aligns to a **real head
  CT** (the 3D Slicer "CT-MR Brain" sample, run through the pipeline, so only the **defaced**
  gold is shipped). The camera dives from the whole body into the head. The only big turn is to
  profile for the face cut, shown as a coral sweep sheet.
- **Motion:** scroll progress is damped exponentially (τ 0.15 s), and everything is a pure
  function of one progress `t`. `window.__journey` exposes it for tests. Reduced motion or no
  WebGL gives stacked static chapters.

---

## 10. Data and licences

**Scans:**

| Use | Source | Licence |
|---|---|---|
| Head CT (landing, demo) | 3D Slicer sample "CT-MR Brain", verified by sha256 | Donated "to be used without any restrictions" |
| Chest | TCIA LIDC-IDRI-0001 | CC BY 3.0 (TCIA data usage policy recorded) |
| Abdomen | TCIA C4KC-KiTS-00002 | CC BY 3.0 |
| Built-in samples and tests | Synthetic phantoms, pydicom test data | — |

**Licensing rules:**
- **Scans are never committed.** They are fetched by `scripts/fetch_public_ct.py` and
  `scripts/load_demo_studies.py`; `sample_data/` and `viewer/public/data/` are gitignored.
- **A head sample was rejected.** The Medimodel sample was "All Rights Reserved", and its launcher
  was deleted.
- **The anatomy atlas** is CC-BY-SA 4.0 with credits, minus the excluded parts.
- **Fonts and icons:** Geist is OFL; the Phosphor icons and three.js are MIT.
- `LICENSES.md` lists everything.

---

## 11. Running it locally

**Requirements:** Docker Desktop with about 8 GB of memory. A webcam is optional.

**Windows:**
1. `Start Demo.bat`. It creates `.env` and `secrets/` with random values via
   `scripts/setup-demo.ps1`, builds, starts, and opens the browser.
2. `Load 3D Studies.bat` fetches and loads the three public studies (15–25 minutes the first time).
3. `Stop Demo.bat` stops it; `Reset Demo.bat` wipes the data.

**macOS / Linux:**

```bash
bash scripts/setup-demo.sh
docker compose up -d --build
docker compose exec api python scripts/load_demo_studies.py
```

**Ports:**
- The app is at `http://localhost:<DEMO_PORT>/`, picked free on first setup (8001 on the dev
  machine).
- Postgres is on host port **5433**, because a native Postgres owns 5432.
- MinIO is on 9000/9001.
- Everything is bound to `127.0.0.1`. Locally there is **no login**, by design.

**Tests:**

```bash
pytest -m "not integration and not ocr"                     # unit
PIPELINE_INTEGRATION_TESTS=1 pytest tests/integration tests/proofs   # needs the stack
docker compose --profile test run --rm test                 # integration + OCR inside the image
cd viewer && npm test && npx tsc -b                          # vitest + types
ruff check . && mypy src
```

---

## 12. Deployment (built, not yet provisioned)

**Decisions** (by the user): a public read-only showcase plus one admin; free hosting; a free
domain; a private GitHub repo.

| Need | Choice |
|---|---|
| Server | Oracle Cloud **Always Free Ampere A1** (ARM64, 4 OCPU / 24 GB, Ubuntu 24.04) |
| Domain | **DuckDNS** (`<name>.duckdns.org`, wildcard subdomains included) |
| HTTPS | **Caddy 2** with automatic Let's Encrypt (HTTPS is also needed for the webcam) |
| CI | **GitHub Actions** (`.github/workflows/ci.yml`) |
| Uptime | UptimeRobot on `/health` |

**Public mode** (`VESALIUS_PUBLIC=1`, in `src/api/auth.py`):
- **Visitors can look at everything:** landing, app, studies, atlas, lineage, reports, proof
  verdicts, API docs and gold downloads.
- **Admin-only routes:**
  - `POST /demo/runs/sample` and `/demo/runs/upload`;
  - `POST /demo/proofs/{key}`;
  - `/ingest`;
  - `/ai/explain/*`.
  They return 401 to anyone else. A unit test walks every route and fails if a mutating route
  lacks `require_admin`. It flattens FastAPI's `_IncludedRouter` via `original_router`.
- **The admin login:**
  - one password, stored as a **scrypt hash** `scrypt:<salt>:<dk>` in `.env`. The separator is
    `:` because Compose interpolates `$` in `.env`, which would corrupt the hash;
  - an HMAC-signed session cookie `vesalius_admin`: HttpOnly, Secure, SameSite=Strict, 12 h;
  - 5 failed logins per IP per 10 minutes, then 429.
- **Creating the hash:** `python -m api.auth hash`.
- **The study loader in public mode:** `scripts/load_demo_studies.py` signs its own admin session
  from `VESALIUS_SESSION_SECRET`.
- **Without `VESALIUS_PUBLIC`,** everything behaves exactly as in the local demo.

**`docker-compose.prod.yml`** (an overlay; needs Compose v2.24+ for `!reset`):
- Caddy is the **only** service with host ports (80/443); postgres, minio and the api have none.
- The api runs uvicorn with `--proxy-headers --forwarded-allow-ips *`, so the rate limit sees real
  client IPs. That is safe only because Caddy is the one thing that can reach the api.
- Memory limits: api 16 GB, postgres and minio 2 GB each, Caddy 256 MB. Restart policies are set
  and logs rotate.
- The hash and session secret are required, so the stack refuses to start without them.

**`deploy/Caddyfile`:**
- `<domain>` proxies to the api.
  - A strict CSP: `default-src 'self'`, `script-src 'self' 'wasm-unsafe-eval'` for MediaPipe, and
    style `'unsafe-inline'` for React and three.js.
  - Images and connections are allowed to `https://files.<domain>` and `blob:`; frames are denied.
  - A looser CSP applies only to `/docs` and `/redoc` (Swagger from jsdelivr).
  - HSTS, nosniff and Permissions-Policy (camera self only).
  - Request bodies capped at 210 MB.
- `files.<domain>` proxies to MinIO **only** for `GET/HEAD/OPTIONS /gold/*`; everything else gets
  404. Bronze, silver, quarantine and the MinIO console are unreachable.

**Scripts** (`scripts/deploy/`, all pass shellcheck):
- `setup-server.sh <name>.duckdns.org <token>`:
  - installs Docker from Docker's apt repo;
  - adds iptables rules for 80/443 (Oracle images reject by default) and 4 GB of swap;
  - installs a DuckDNS cron updater;
  - writes `.env` (random secrets via `setup-demo.sh`, the domain, the session secret).
- `deploy.sh [--studies]`:
  - `git pull`, then build;
  - on the first run, prompts for the admin password; only the hash reaches `.env`;
  - `up -d`, waits for health, and can load the public studies.
- `backup.sh`: a nightly `pg_dump` plus silver and gold mirrors, kept for 7 days. Bronze is
  deliberately not copied.

**CI jobs:**
- `python`: ruff, mypy and the unit tests on a bare runner. Rehearsed in a clean container: all
  pass.
- `viewer`: npm ci, vitest and the build.
- `integration`: the full api image, plus `docker compose --profile test run --rm test` against
  real Postgres and MinIO.

**Verified so far:**
- the compose merge;
- `caddy validate`;
- the 5 static deploy tests, which mutation-check that only Caddy has ports and that the files
  host is gold-read-only;
- the auth tests (19).

**ARM64:** every dependency has aarch64 wheels, checked with `pip download --platform`, including
torch 2.14 CPU. S8 and S10 timings on A1 cores are **unmeasured** until the first server run.

**The user's remaining steps** (DEPLOY.md):
1. Create an empty private GitHub repo and run `git push -u origin phase-one-three:main`.
2. Sign up for Oracle Cloud and DuckDNS.
3. Create the A1 VM and open 80/443 in the VCN security list.
4. Run `setup-server.sh`, then `deploy.sh --studies`.
5. Run the curl and nmap checks, then sign in and run a proof.

---

## 13. Tech stack and versions

- **Pipeline:**
  - **Core:** Python 3.11, FastAPI, Pydantic, SQLAlchemy and Alembic, PostgreSQL 16, MinIO.
  - **Imaging and meshes:** pydicom (de-identification, with full tag control), SimpleITK
    (resampling and NIfTI; *not* its series reader in S4), NumPy, SciPy, scikit-image (marching
    cubes), trimesh, **fast-simplification 0.2.0**.
  - **Other:** pytesseract plus the tesseract binary (only in the image), Pillow.
  - **Segmentation:** TotalSegmentator 2.18 with torch 2.14.0+cpu and torchvision 0.29.0+cpu.
- **Orchestration:** a stage runner (`run_stage`) inside the api. Dagster was in the original
  stack but is not used.
- **Infrastructure:** Docker Compose; Caddy 2 in production. The api image is about 4.2 GB.
- **App:** React 19, Vite 8, TypeScript 6, three.js, R3F, Drei, Zustand, MediaPipe tasks-vision,
  vitest.
- **Quality:** ruff, mypy, pytest, and mutation checks: guarded code is deliberately broken to prove each test fails.
- **Determinism** (needed for proof 3): fixed epoch timestamps in file headers, `sort_keys=True`
  JSON, the parameter-set seed for any randomness, and a single-threaded decimator.

---

## 14. Key decisions log

| When | Decision | Why |
|---|---|---|
| Phase 1 | Volumes in **RAS** from S6 on (manifest `source_frame: RAS_mm`); `RAS_TO_GLTF = [[1,0,0],[0,0,1],[0,-1,0]]` | GEOMETRY.md's `LPS_TO_GLTF` assumes LPS input; using it literally mirrors X |
| Phase 1 | Taubin `nu` sign corrected to +0.53 | The documented −0.53 shrinks meshes under trimesh's implementation (measured) |
| Phase 1 | S8 morphology border effect left unfixed and documented | Both fixes tried were auto-repair (rule 6) |
| Phase 1 | Proofs rewritten after inheriting broken ones | Proof 3 didn't import; proof 2 only tested a pure function |
| Phase 2 | Structures mode with TotalSegmentator; **facial bone is cut** using S7's verified region | The skull otherwise carries the facial skeleton, which is identifying |
| Phase 2 | mesh_contract 1.0 not edited after publishing | Contracts are immutable |
| Phase 2 | Vision-AI path removed; measurements computed locally | Rule 8 |
| Phase 3 | **One product:** landing at `/` and one React app at `/app`; old console and viewer redirect | The user wanted one dashboard that does everything Holoscan did |
| Phase 3 | The full atlas copied into the repo, minus parts under non-commercial or unclear licences | It must work standalone and be shippable |
| Phase 3 | Surgical-green light theme everywhere | The user rejected the dark look |
| Phase 3 | The landing rewritten as 5 chapters, damped scroll, minimal rotation | The old version jumped abruptly and felt generic |
| Deploy | Oracle A1 ARM + DuckDNS + Caddy | The only free tier with enough RAM for TotalSegmentator |
| Deploy | **pymeshlab → fast-simplification** | No aarch64 build of pymeshlab |
| Deploy | Public mode plus a single admin; locally unchanged | A showcase must not accept public uploads (rule 1) or let anyone burn CPU |
| Deploy | Hash separator `:` | Compose `$` interpolation would silently break the login |

---

## 15. Known gaps and open findings

- **Gold is "disposable" only in theory.** The gold MinIO credential has no `DeleteObject`, so
  only MinIO root can delete gold. Proof 3 overwrites gold instead of deleting it. Not yet decided.
- **Face-tip fragments:** S7 leaves small fragments in the served gold **volume**. The meshes are
  clean.
- **Stale studies:** the studies served on the dev machine were built with S10 v1 (pymeshlab).
  They need a re-export to match S10 v2.
- **Boundary preservation:** fast-simplification has none (a residual risk).
- **Unmeasured:** ARM64 performance, RAM and timings.
- **Batch link:** `instances.ingest_batch_id` is NULL in some paths.
- **Contract v1.1 (LPS)** is deliberately not published; proof 4 publishes it only inside a
  rolled-back transaction.
- **Not on the main checkout yet:** the user's main checkout (`master`) holds an older uncommitted
  copy. The authoritative state is branch `phase-one-three`.
- **Out of scope:** multiple users or roles, scaling, a CDN, clinical use, and viewer roadmap
  items (voice, AR, quiz, unassigned swipe actions).

---

## 16. Environment gotchas (dev machine: Windows 11, Docker Desktop)

- **Ports:** a native Postgres owns 5432 (use 5433), and port 8000 is taken by another project
  (the demo uses 8001).
- **tesseract** exists only inside the Docker image, so OCR tests run there.
- **Memory:** the machine has about 15 GB and is often under pressure. Run tests one file at a
  time, and never overlap mutation sweeps with other runs.
- **Git:** the F: drive has no ownership info, so git needs `-c safe.directory=*` (or run it from
  PowerShell).
- **Line endings:** shell scripts must be LF (`.gitattributes`); a CRLF entrypoint breaks the
  container.
- **Scripting:** Bash heredocs on Windows mangle `\n` and quotes, so write scripts to files.
- **Images:** MinIO is built from its final upstream source release (`docker/minio/Dockerfile`,
  image `vesalius-minio:<release>`, shared by `minio` and `minio-init`): `quay.io/minio/*` needs a
  login since MinIO stopped publishing images. Copy files out with `docker compose cp`.
- **Tests and the database:** the dev database holds rows from earlier runs, so tests must scope
  assertions to their own ids.
- **FastAPI 0.141** wraps included routers in `_IncludedRouter`, so route walks must recurse into
  `original_router`.

---

## 17. Glossary

- **DICOM:** the medical imaging file format; one file per slice, with tags (metadata).
- **PHI:** protected health information (names, IDs, dates, burned-in text).
- **HU:** Hounsfield units, the CT intensity scale (air −1000, water 0, bone 300+).
- **LPS / RAS:** patient coordinate conventions (Left-Posterior-Superior vs Right-Anterior-Superior).
- **sform / affine:** NIfTI's voxel-index → world-mm matrix. A negative determinant means mirrored.
- **Gate:** a stage that refuses to let data through unless a prior verified step completed.
- **Preset:** an HU threshold set for segmentation (bone, skin surface and so on). "Structures"
  means named AI segmentation.
- **Manifest:** the gold JSON beside each export: frame, parameters, provenance, deface status.
- **Presigned URL:** a time-limited signed link straight to an object in MinIO (gold only).
- **Medallion (bronze/silver/gold):** raw → cleaned and validated → served products.
- **Holoscan:** the original gesture-controlled 3D viewer project, now merged into Vesalius's app.

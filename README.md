# Vesalius

Turns medical scans into 3D models that are safe to share, and proves it.

A governed data pipeline for CT and MR imaging: DICOM ingest, de-identification across three
channels, face removal before any 3D surface exists, and NIfTI / GLB output with full lineage.
One app runs it all: start a scan, watch every stage, then explore the result full screen as a
hologram you steer with your hands (the Holoscan engine), next to a full-body anatomy atlas.

> De-identifying the metadata of a scan is not the same as de-identifying its output. A 3D
> surface from a head CT is a recognisable face. This pipeline closes that channel.

Everything runs on your own machine. It uses only public, openly licensed scans.

---

## Quick start

You need **Docker Desktop**, running, with about **8 GB of memory** available to it (the usual default;
Docker Desktop > Settings > Resources). A webcam is optional: it enables hand-gesture control of
the 3D hologram (the mouse always works).

### Windows

1. Download or clone this folder.
2. Double-click **`Start Demo.bat`**. The first start builds everything, which takes 5 to 10
   minutes. Later starts take under a minute. Your browser opens when it is ready.
3. Double-click **`Load 3D Studies.bat`** to load the three public example scans (a head, a
   chest and an abdomen) into the app's study library. The first time takes 15 to 25 minutes: it downloads
   about 250 MB of scans and the segmentation model. It is safe to run again, and it skips what is
   already loaded.

To stop: **`Stop Demo.bat`**. To wipe every stored scan and start fresh: **`Reset Demo.bat`**.

### macOS / Linux

```bash
bash scripts/setup-demo.sh                       # once: creates .env and secrets/ with random passwords
docker compose up -d --build                     # first time 5-10 minutes
docker compose exec api python scripts/load_demo_studies.py   # loads the 3D studies (15-25 min first time)
```

The address is printed by the setup script: `http://localhost:<DEMO_PORT>/` (usually 8000).
Stop with `docker compose down`. Wipe everything with `docker compose down --volumes`.

---

## What to try

The landing page (`/`) tells the story; **Open Vesalius** goes to the app (`/app`), where everything
else lives:

| Screen | Address | What it does |
|---|---|---|
| **Dashboard** | `/app` | What has been published, which proofs pass, and where to go next |
| **Run a scan** | `/app/pipeline` | Run a built-in sample, or upload your own **public** scan as a `.zip` of DICOM files. Choose *Bone*, *Skin surface* or *Named structures*. Watch every stage live; the result shows what was removed, an audit trail, and **Enter hologram** |
| **Studies** | `/app/studies` | Every published 3D study. Open one to explore it full screen |
| **Hologram** | `/app/hologram/<id>` | One study, full screen (**F**). Each organ, bone and vessel is its own layer; select one for its measurements; the audit trail shows every stage that made it. A head study has no face: that is the point |
| **Full body** | `/app/body` | The full-body anatomy atlas (8 systems: skeletal, cardiovascular, visceral, muscular, nervous, lymphoid, joints, regions) with a description for each structure |
| **Proofs** | `/app/proofs` | Five automated tests, each trying to break one promise. Run any of them and read the verdict |
| **API docs** | `/docs` | The read-only serving API |

**Hologram controls.** Mouse: drag to rotate, scroll to zoom, right-drag to pan, click a part to
select it. **Hands** (webcam, allow the camera prompt): pinch to rotate, open palm to pan, pinch with
both hands to zoom, hold a fist to freeze, then point with the left index finger and make a victory
sign to select; a right-hand swipe isolates the part. **How to use** (bottom) lists every gesture.
The camera runs only on the hologram screens, and the hands chip (top right) turns it off.

**Optional AI explanations.** Put a key in `.env` as `GROQ_API_KEY=...` and restart. A scan's
structure then gets an AI explanation of its **measurements** (numbers only, never pictures); the
full-body atlas also accepts a snapshot of the view. Without a key the app says the AI is off and
everything else works. The key stays on the server.

**Uploading your own scan.** Only public or fully anonymous data, never real patient scans. The
app asks you to confirm this. *Named structures* works on CT only.

### If something goes wrong

- **"Docker Desktop is not running"**: open Docker Desktop, wait for *Engine running*, try again.
- **The page does not open**: run `docker compose logs --tail 50 api` and look at the last lines.
- **A 3D study fails at "segment"**: usually too little memory for the segmentation model. Give
  Docker about 8 GB or more and run `Load 3D Studies.bat` again.
- **The hologram says "Refused"**: it is doing its job. It will not show a head study whose face
  was not removed.
- **No hands**: allow the browser's camera prompt, or use the mouse. The hands chip shows the
  camera's state.
- **Old links**: `/console` and `/viewer/` still work; they open the app.
- **Port already in use**: the setup picks a free port once and stores it as `DEMO_PORT` in `.env`.
  Change it there if you need to.

---

## Putting it online

A free public deployment (read-only showcase + one admin login, HTTPS, CI) is described step by
step in [DEPLOY.md](DEPLOY.md). Locally nothing changes: no login, loopback only.

## Data and licences

The scans are fetched at run time from their public sources and never committed:
3D Slicer's *CT-MR Brain* sample (donated for unrestricted use), TCIA LIDC-IDRI and C4KC-KiTS
(CC BY 3.0). The segmentation model is TotalSegmentator (Apache-2.0, `total` task only). The
full-body atlas (the app and the landing page) is Z-Anatomy / BodyParts3D (CC-BY-SA, via Holoscan),
at full detail, with the parts under non-commercial or unstated licences removed
(`assets/atlas/EXCLUDED.md`). Details in **`LICENSES.md`**.

---

## For developers

Specification: `CLAUDE.md` and `refs/`. The pipeline lives in `src/` and `tests/`; the app in
`viewer/` (served at `/app`); the atlas in `assets/atlas/` (`python scripts/build_atlas.py
<holoscan folder>` rebuilds it, byte for byte).

### Stages (S1-S12)

| Stage | Purpose |
|---|---|
| **S1** Ingest | Archive upload, DICM magic validation, SHA-256 content addressing, bronze storage |
| **S2** Parse & Index | Metadata extraction (stop_before_pixels), instance/series/study grouping, catalog |
| **S3** Quality Gate | 12 reason-coded checks (geometry, spacing, modality, duplicates) |
| **S4** De-identify | Tag profile, deterministic UID remapping, date shifting, private tag removal, pixel OCR redaction |
| **S5** Verify | Independent rescan of de-identified silver; gates S6 |
| **S6** Volume Assembly | Slice ordering by position projection, HU rescaling, affine from IOP, NIfTI output |
| **S7** Deface | Geometric defacing of head anatomy; gates S9 (fail-closed) |
| **S8** Segment | Threshold presets, or *structures* mode: TotalSegmentator labels, grouped by body system, cut to S7's face region |
| **S9** Surface Extraction | Marching cubes on the defacing-gated volume (one mesh per structure in structures mode) |
| **S10** Mesh Post-process | Taubin smoothing and quadric decimation, per-group face budgets in structures mode |
| **S11** Export | GLB (glTF axis conversion), one multi-node GLB per body system + `structures.json`, thumbnail, manifest |
| **S12** Serve | FastAPI endpoints incl. `/studies` for the viewer, lineage, presigned gold-only URLs, role-bounded serving |

### Local setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env            # fill in real secrets (or run the setup script above)
docker compose up -d postgres minio minio-init migrate
```

CLI: `python -m pipeline.cli ingest --source my-upload path/to/archive.zip`, then
`python -m pipeline.cli parse-index <batch-id>`.

### App

```bash
cd viewer
npm ci
npm run dev      # http://localhost:5173/app/, proxies the API (VESALIUS_API, default :8001)
npm test         # vitest: mesh-contract validation (studies, atlas, surfaces), layers, anatomy lookup
npm run build    # the Docker image does this in its Node stage and serves it at /app/
```

`npm run build -- --mode pages` builds the static site's app (no server; DEPLOY.md part A). Its
data comes from `python scripts/export_static.py --api http://localhost:<port>` (gitignored).

### Tests

```bash
pytest tests/unit -v
PIPELINE_INTEGRATION_TESTS=1 pytest tests/integration tests/proofs -v   # needs the stack running
```

Integration tests and proofs use the real Postgres and MinIO. Run them one file at a time on
machines with little memory.

### The five proofs (`tests/proofs/`)

| Proof | File | Claim |
|---|---|---|
| 1 PHI injection | `test_phi_injection.py` | Identifiers are removed across all 3 channels (tags, private, pixels). Reported as N of N. |
| 2 Defacing gate | `test_defacing_gate.py`, `test_defacing_gate_system.py` | The pipeline cannot emit an identifying face mesh; the gate fails closed. |
| 3 Reproducibility | `test_reproducibility.py` | Gold is disposable: delete it, rebuild from silver, hashes are identical. |
| 4 Contract violation | `test_contract_violation.py`, `test_contract_violation_published.py` | Consumers refuse upstream schema drift, above all a silent left-right mirror. |
| 5 Viewer has no face | `test_head_study_has_no_face.py` | A head study, loaded the way the viewer loads it, has no vertex in the face region (and the check finds the face when the cut is disabled). |

### Quality gates

```bash
ruff check .
mypy src/
cd viewer && npx tsc -b && npm test
```

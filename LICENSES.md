# Third-party licences

Everything shipped or run by this project, with its licence and the evidence checked. Add a
row **before** introducing any new model, dataset, asset or library. If a licence cannot be
confirmed, do not ship it.

## Models

| What | Licence | Evidence | Notes |
|---|---|---|---|
| TotalSegmentator (code) | Apache-2.0 | github.com/wasserth/TotalSegmentator (repo licence) | Runs in S8 structure mode |
| TotalSegmentator `total` task weights | Apache-2.0 | same README, "free tasks" list | **Only `total` is used.** Licensed subtasks (`face`, `brain_structures`, `appendicular_bones`, `tissue_types`, `heartchambers_highres`, `coronary_arteries`, ...) must not be enabled |

## Imaging data (never committed; fetched into the gitignored `.cache/`)

| Dataset | Licence | Evidence | Used for |
|---|---|---|---|
| 3D Slicer sample "CT-MR Brain" (`CT-brain.nrrd`, sha256 `6a5b6cac…`) | Donated "to be used without any restrictions" | Slicer `SampleData.py` | Landing page head scan (defaced gold output only ships) |
| TCIA LIDC-IDRI, patient LIDC-IDRI-0001, one chest CT series (133 images) | CC BY 3.0 | TCIA API `LicenseName` per series, checked by `scripts/fetch_public_ct.py`; DOI 10.7937/K9/TCIA.2015.LO9QL9SX | Phase two chest study |
| TCIA C4KC-KiTS, patient KiTS-00002, one abdomen CT series (256 images) | CC BY 3.0 | same; DOI 10.7937/TCIA.2019.IX49E8NX | Phase two abdomen study |
| Static site studies (`site-data`, the `static-data` release; never committed) | as above, per study | pipeline gold output of the three studies above, exported by `scripts/export_static.py` (refuses any study without a known public source); each study's `CREDIT.txt` travels with it, and the Studies screen and the hologram show its credit and DOI | The static site (GitHub Pages, DEPLOY.md part A) |

**TCIA Data Usage Policy** (shipped inside every TCIA download, recorded in the provenance
JSON): acknowledge the dataset and its DOI wherever the data is presented, and never use it to
identify participants, "including generating facial images or comparable representations".
The viewer and landing credits show the collection and DOI for every TCIA study.

## 3D atlas (Holoscan viewer)

Source: Z-Anatomy, derived from BodyParts3D (`Holoscan/refs/3D/PC/Resources/Models/License.txt`).
Required attribution for anything copied or derived:

- "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
- "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"

Derived works must be distributed under the same licence (share-alike).

**Excluded: never ship these parts or anything derived from them.**

| Included model | Licence | Why excluded |
|---|---|---|
| "Anatomy of the Inner Ear" (University of Dundee) | CC-BY-NC-SA 4.0 | non-commercial |
| "Kidney" (lissiecowley) | CC-BY-NC 4.0 | non-commercial |
| "Brainder", "White matter" (University of Washington) | not stated | licence unknown |

"Cranial Nerves and Foramina" (University of Dundee, CAHID) is CC-BY 4.0 and may ship with
attribution.

**What ships on the landing page:** `src/api/static/body/`, the anatomy statue: the skeletal,
muscular and cardiovascular systems and a short list of named organs from the licence-cleaned
`assets/atlas/` GLBs, merged and decimated by `scripts/build_landing_body.py`, licensed CC-BY-SA 4.0
with `body/CREDITS.txt` and a footer credit. On top of `assets/atlas/`'s removals, the build drops
every kidney/renal and ear node again by name, leaves the nervous system out entirely, and keeps
only named organs from the visceral system. `tests/unit/test_demo_static_architecture.py` checks
that the filters cover every row of `assets/atlas/EXCLUDED.md` and that only those four source
files ship.

**The full atlas (phase three, `/app/body`):** `assets/atlas/` holds all 8 Holoscan systems at
original detail (155 MB), built by `scripts/build_atlas.py`, CC-BY-SA 4.0 with `CREDITS.txt`, the
Z-Anatomy `LICENSE` and the models' `License.txt`. Removed by whole scene subtree, listed node by
node in `assets/atlas/EXCLUDED.md`: the cerebrum (`Telencephalon.g`: Brainder cortical parcels and
telencephalic white matter), the inner ear plus, conservatively, the ossicles and tympanic
membranes, every other white-matter subtree (spinal cord tracts, diencephalon), and the kidneys
with their intrarenal vessels. Every kept mesh is byte-identical to the
source. Structure descriptions (`descriptions.json`) are Z-Anatomy's Wikipedia-derived texts,
CC-BY-SA. `tests/unit/test_atlas_assets.py` checks the exclusions on the shipped bytes.

## Libraries and runtime assets

| What | Licence | Where |
|---|---|---|
| three.js r186 | MIT | `src/api/static/vendor/` |
| Geist, Geist Mono | OFL-1.1 | `src/api/static/fonts/` |
| Playfair Display Italic (landing accent words; the 3D room's structure names) | OFL-1.1 | `src/api/static/fonts/` and `viewer/src/assets/fonts/`, licence in `OFL-PlayfairDisplay.txt` beside each |
| Phosphor icons | MIT | `src/api/static/icons.svg` |
| MediaPipe tasks-vision (+ hand landmarker model) | Apache-2.0 | vendored into `viewer/public/` (no CDN) |
| App npm dependencies (React, React Router, R3F, Drei, Zustand, @phosphor-icons/react) | MIT | `viewer/package.json` |
| Geist, Geist Mono (app copy) | OFL-1.1 | `viewer/src/assets/fonts/`, bundled into the app build |
| Groq API (optional AI explanations) | external service, off by default | used only when `GROQ_API_KEY` is set on the server; scans send measurements only (CLAUDE.md rule 8) |
| Vitest (dev only) | MIT | `viewer/package.json` devDependencies |
| MinIO server `RELEASE.2025-10-15T17-29-55Z`, MinIO client `RELEASE.2025-08-13T08-35-41Z` | AGPL-3.0 | built unmodified from the upstream tags by `docker/minio/Dockerfile` (upstream stopped publishing images and archived both repositories); the source is the public tag, so no change of ours needs offering |

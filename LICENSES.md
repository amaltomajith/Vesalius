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
| Viewer demo study (`viewer/public/demo/chest`) | CC BY 3.0 (LIDC-IDRI, as above) | pipeline gold output of the LIDC chest series, rebuilt by `scripts/bundle_viewer_demo.py`, never committed; `CREDIT.txt` travels with it and the picker shows the DOI | Viewer runs without a pipeline instance |

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

**What ships:** `src/api/static/atlas/` (landing page): a point cloud sampled from the skeletal,
cardiovascular and nervous GLBs by `scripts/build_landing_atlas.py`, licensed CC-BY-SA 4.0 with
`atlas/CREDITS.txt` and a footer credit. Exclusion is conservative because node names cannot prove
provenance: no visceral system (holds the NC kidney), no kidney/renal or ear nodes anywhere, and no
nervous-system point inside the cranium ("Brainder", "White matter"). A unit test checks the last
two on the shipped bytes.

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
| Phosphor icons | MIT | `src/api/static/icons.svg` |
| MediaPipe tasks-vision (+ hand landmarker model) | Apache-2.0 | vendored into `viewer/public/` (no CDN) |
| App npm dependencies (React, React Router, R3F, Drei, Zustand, @phosphor-icons/react) | MIT | `viewer/package.json` |
| Geist, Geist Mono (app copy) | OFL-1.1 | `viewer/src/assets/fonts/`, bundled into the app build |
| Groq API (optional AI explanations) | external service, off by default | used only when `GROQ_API_KEY` is set on the server; scans send measurements only (CLAUDE.md rule 8) |
| Vitest (dev only) | MIT | `viewer/package.json` devDependencies |

# Medical Imaging Pipeline

Governed data pipeline for clinical imaging: DICOM ingest, de-identification, and
NIfTI/GLB export with full lineage and reproducibility. See `CLAUDE.md` and `refs/` for
the full specification.

## Stages implemented (S1-S12, complete)

| Stage | Purpose |
|---|---|
| **S1** Ingest | Archive upload, DICM magic validation, SHA-256 content addressing, bronze storage |
| **S2** Parse & Index | Metadata extraction (stop_before_pixels), instance/series/study grouping, catalog |
| **S3** Quality Gate | 12 reason-coded checks (geometry, spacing, modality, duplicates) |
| **S4** De-identify | Tag profile, deterministic UID remapping, date shifting, private tag removal, pixel OCR redaction |
| **S5** Verify | Independent rescan of de-identified silver; gates S6 |
| **S6** Volume Assembly | Slice ordering by position projection, HU rescaling, affine from IOP, NIfTI output |
| **S7** Deface | Geometric defacing of head anatomy; gates S9 (fail-closed) |
| **S8** Segment | Threshold-based HU segmentation, NIfTI mask output |
| **S9** Surface Extraction | Marching cubes on the defacing-gated volume |
| **S10** Mesh Post-process | Largest component, degenerate removal, Taubin smoothing, quadric decimation, normals |
| **S11** Export | GLB (LPS->glTF axis conversion), gold volume copy, thumbnail, manifest |
| **S12** Serve | FastAPI endpoints, recursive lineage query, presigned URLs, role-bounded serving |

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env            # fill in real secrets
```

## Running the stack

```bash
docker compose up -d postgres minio minio-init migrate
```

## CLI

```bash
python -m pipeline.cli ingest --source my-upload path/to/archive.zip
python -m pipeline.cli parse-index <batch-id>
```

## Tests

```bash
pytest tests/unit -v          # no Docker required
pytest tests/proofs -v        # proofs 2 and 4 also need no Docker
```

Some tests under `tests/unit/` and all of `tests/integration/` exercise real
Postgres/MinIO behavior (the catalog schema uses Postgres-specific JSONB/ARRAY/UUID
columns that don't run on SQLite). These are marked `@pytest.mark.integration` and skip
automatically unless `PIPELINE_INTEGRATION_TESTS=1` is set, after bringing up the stack:

```bash
docker compose up -d postgres minio minio-init migrate
PIPELINE_INTEGRATION_TESTS=1 pytest tests/unit tests/integration tests/proofs -v
```

## The four proofs (tests/proofs/)

| Proof | File | Claim |
|---|---|---|
| 1 PHI injection | `test_phi_injection.py` | Pipeline removes identifiers across all 3 channels (tags, private, pixels). Reports removal rate as N of N. |
| 2 Defacing gate | `test_defacing_gate.py` | Pipeline cannot emit an identifying face mesh. Gate fails closed; non-head records explicit reason. |
| 3 Reproducibility | `test_reproducibility.py` | Gold is disposable; lineage is real. Delete gold, rebuild from silver, hashes are identical. |
| 4 Contract violation | `test_contract_violation.py` | Consumers are protected from upstream schema drift (LPS vs RAS orientation, version bump, manifest mismatch). |

## Quality gates

```bash
ruff check .
mypy src/
```

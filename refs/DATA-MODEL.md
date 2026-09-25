# Data Model

Catalog schema, lineage, and the published data contract.

**Governing principle:** bronze and silver are immutable. Gold is disposable and rebuildable.
The catalog is the only mutable state, and every mutation is append-only where it records
history.

---

## PHI boundary

The catalog spans the PHI boundary. Two schemas, different access:

| Schema | Contents | Access |
|---|---|---|
| `restricted` | Original UIDs, original patient ID, date-shift offsets, deid audit | Pipeline service account only. Never exposed via API |
| `public` | Pseudo UIDs, geometry, quality, artifacts, lineage | Readable by the serving layer |

Enable row-level security on `restricted`. The serving layer's database role must have no
grant on it at all — not a filtered view, no grant.

---

## Schema

```sql
-- ========================= restricted =========================

CREATE SCHEMA restricted;

CREATE TABLE restricted.uid_map (
    original_uid      TEXT PRIMARY KEY,
    pseudo_uid        TEXT NOT NULL UNIQUE,
    uid_type          TEXT NOT NULL,          -- sop | series | study | frame_of_reference
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE restricted.patient_map (
    original_patient_id TEXT PRIMARY KEY,
    pseudo_patient_id   TEXT NOT NULL UNIQUE,
    date_offset_days    INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE restricted.deid_audit (
    id             BIGSERIAL PRIMARY KEY,
    instance_id    UUID NOT NULL,
    channel        TEXT NOT NULL,             -- tag | private | pixel | geometry
    tag            TEXT,                      -- '(0010,0010)' when channel = tag
    tag_name       TEXT,
    action         TEXT NOT NULL,             -- remove | replace | uid_remap | date_shift
                                              -- | redact | deface
    rule_id        TEXT NOT NULL,             -- which profile rule fired
    pixel_region   INTEGER[],                 -- [x0,y0,x1,y1] when channel = pixel
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON restricted.deid_audit (instance_id);

-- =========================== public ===========================

CREATE TABLE ingest_batches (
    id                UUID PRIMARY KEY,
    source            TEXT NOT NULL,
    file_count        INTEGER NOT NULL,
    bytes_total       BIGINT NOT NULL,
    pipeline_version  TEXT NOT NULL,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ
);

CREATE TABLE studies (
    id                  UUID PRIMARY KEY,
    pseudo_study_uid    TEXT NOT NULL UNIQUE,
    pseudo_patient_id   TEXT NOT NULL,
    modality            TEXT,
    body_part           TEXT,
    body_part_source    TEXT,      -- tag | inferred | unresolved
    shifted_study_date  DATE,
    deface_required     BOOLEAN NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE series (
    id                   UUID PRIMARY KEY,
    study_id             UUID NOT NULL REFERENCES studies(id),
    pseudo_series_uid    TEXT NOT NULL UNIQUE,
    modality             TEXT NOT NULL,
    instance_count       INTEGER NOT NULL,
    rows                 INTEGER,
    columns              INTEGER,
    pixel_spacing        DOUBLE PRECISION[],   -- [row, column] mm
    slice_spacing        DOUBLE PRECISION,     -- derived, not from tags
    spacing_uniform      BOOLEAN,
    orientation          DOUBLE PRECISION[],   -- ImageOrientationPatient, 6 values
    rescale_slope        DOUBLE PRECISION,
    rescale_intercept    DOUBLE PRECISION,
    quality_status       TEXT NOT NULL,        -- pass | quarantined
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE instances (
    id                 UUID PRIMARY KEY,
    series_id          UUID NOT NULL REFERENCES series(id),
    pseudo_sop_uid     TEXT NOT NULL UNIQUE,
    content_hash       TEXT NOT NULL,          -- sha256 of original bytes
    bronze_key         TEXT NOT NULL,
    slice_position     DOUBLE PRECISION,       -- projected onto slice normal
    burned_in_flag     TEXT,
    ingest_batch_id    UUID REFERENCES ingest_batches(id),
    UNIQUE (content_hash)
);

CREATE TABLE transform_runs (
    id                UUID PRIMARY KEY,
    stage             TEXT NOT NULL,           -- S1..S12
    stage_version     TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL UNIQUE,
    subject_type      TEXT NOT NULL,           -- instance | series | study
    subject_id        UUID NOT NULL,
    parameters        JSONB NOT NULL DEFAULT '{}',
    input_hashes      TEXT[] NOT NULL,
    output_hashes     TEXT[],
    status            TEXT NOT NULL,           -- running | success | failed | skipped
    reason            TEXT,                    -- populated for failed | skipped
    duration_ms       INTEGER,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ
);
CREATE INDEX ON transform_runs (subject_id, stage);

CREATE TABLE artifacts (
    id                  UUID PRIMARY KEY,
    series_id           UUID NOT NULL REFERENCES series(id),
    kind                TEXT NOT NULL,         -- surface_glb | volume_nifti
                                               -- | thumbnail | mask | feature_table
    format              TEXT NOT NULL,
    zone                TEXT NOT NULL,         -- silver | gold
    object_key          TEXT NOT NULL,
    sha256              TEXT NOT NULL,
    bytes               BIGINT NOT NULL,
    contract_version    TEXT,
    produced_by_run     UUID NOT NULL REFERENCES transform_runs(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lineage_edges (
    id               BIGSERIAL PRIMARY KEY,
    downstream_run   UUID NOT NULL REFERENCES transform_runs(id),
    upstream_run     UUID REFERENCES transform_runs(id),
    upstream_artifact UUID REFERENCES artifacts(id),
    CHECK (upstream_run IS NOT NULL OR upstream_artifact IS NOT NULL)
);

CREATE TABLE quarantine (
    id              UUID PRIMARY KEY,
    subject_type    TEXT NOT NULL,
    subject_id      UUID NOT NULL,
    stage           TEXT NOT NULL,
    reason_code     TEXT NOT NULL,             -- see TESTING.md
    detail          JSONB NOT NULL,            -- the actual failing values
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON quarantine (reason_code);

CREATE TABLE data_contracts (
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,
    schema        JSONB NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name, version)
);
```

---

## Lineage

Every artifact answers: *which source, which code, which parameters, when.*

```
artifact -> produced_by_run -> lineage_edges -> upstream runs/artifacts -> ... -> instances
```

**Query — full provenance of any artifact:**

```sql
WITH RECURSIVE prov AS (
    SELECT tr.id, tr.stage, tr.stage_version, tr.parameters, 0 AS depth
    FROM artifacts a JOIN transform_runs tr ON tr.id = a.produced_by_run
    WHERE a.id = $1
  UNION ALL
    SELECT up.id, up.stage, up.stage_version, up.parameters, prov.depth + 1
    FROM prov
    JOIN lineage_edges le ON le.downstream_run = prov.id
    JOIN transform_runs up ON up.id = le.upstream_run
)
SELECT * FROM prov ORDER BY depth;
```

This query backs `GET /series/{id}/lineage` and is worth having working early — it is the
demo that proves lineage is real rather than decorative.

---

## Data contract

The published schema the CV consumer codes against. Version it; breaking changes bump major.

### `volume_contract` v1.0

```json
{
  "name": "volume_contract",
  "version": "1.0",
  "artifact_kind": "volume_nifti",
  "format": "nifti-1-gzip",
  "array": {
    "dimensions": 3,
    "axis_order": ["z", "y", "x"],
    "dtype": "float32"
  },
  "spatial": {
    "coordinate_frame": "RAS",
    "spacing_units": "mm",
    "isotropic": true,
    "affine": "stored in NIfTI header, srow_x/y/z"
  },
  "intensity": {
    "units": "hounsfield_units",
    "applies_to_modality": ["CT"],
    "rescale_applied": true,
    "air_value": -1000.0,
    "water_value": 0.0
  },
  "nullability": {
    "padding_value": -1024.0,
    "note": "Regions outside the reconstruction circle are padded, not missing"
  },
  "guarantees": [
    "de-identified per PS3.15 basic profile",
    "defaced when body_part requires it",
    "slice order validated by position projection",
    "spacing uniformity verified"
  ]
}
```

### Consumer-side validation

The CV consumer validates on read and **refuses** on mismatch. This is the point of the
contract — demonstrate it working.

```python
def validate(contract: dict, header, array) -> None:
    if contract["version"].split(".")[0] != SUPPORTED_MAJOR:
        raise ContractViolation(f"major version {contract['version']} unsupported")
    if contract["spatial"]["coordinate_frame"] != "RAS":
        raise ContractViolation("expected RAS orientation")
    if contract["intensity"]["units"] != "hounsfield_units":
        raise ContractViolation("expected HU intensity")
    if array.ndim != 3:
        raise ContractViolation(f"expected 3 dimensions, got {array.ndim}")
```

**Demonstration:** publish v1.1 with `coordinate_frame` changed to `LPS`, show the consumer
reject it at load rather than silently producing mirrored results. Silent left-right mirroring
in medical imaging is exactly the class of bug contracts exist to prevent.

---

## Storage layout

```
bronze/   {hash[:2]}/{hash}.dcm                      immutable, PHI, restricted
silver/   {series_id}/volume.nii.gz
          {series_id}/meta.json
gold/     {series_id}/surface.glb
          {series_id}/volume.nii.gz
          {series_id}/thumbnail.png
          {series_id}/manifest.json
quarantine/ {subject_id}/...                         with reason in catalog
```

Content-addressed bronze gives free deduplication. Series-addressed silver and gold make the
rebuild test trivial: delete `gold/{series_id}/`, re-run from silver, compare hashes.

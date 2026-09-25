# Pipeline Stages

Twelve stages. Each has a defined input, output, failure mode, and idempotency key.
**A stage must never partially write.** Either it produces its complete output and records a
`transform_run`, or it fails and writes to quarantine.

Idempotency rule throughout: `key = sha256(input_hashes + stage_name + stage_version + params)`.
If a `transform_run` with that key exists and succeeded, skip and reuse the output.

---

## S1 — Ingest

**In:** uploaded archive (zip/tar) or directory of files
**Out:** files landed in `bronze/{content_hash[:2]}/{content_hash}.dcm`, `ingest_batches` row,
one `instances` row per file

- Reject non-DICOM early: check for the `DICM` magic at byte offset 128
- Compute SHA-256 of the **full file bytes** before anything else. This is the identity of
  the instance forever
- Duplicate hash → record the reference, do not re-store, do not re-process
- Bronze is **write-once**. Never modify a file in bronze
- Bronze is PHI-bearing. Separate bucket, separate credentials, never served publicly

**Fails on:** unreadable archive, zero DICOM files found

---

## S2 — Parse & Index

**In:** bronze instances
**Out:** `studies`, `series`, `instances` populated with metadata

- Read with `pydicom.dcmread(path, stop_before_pixels=True)` for indexing — do not decode
  pixel data here, it is slow and unnecessary
- Group: instance → series via `SeriesInstanceUID`, series → study via `StudyInstanceUID`
- Capture at minimum: `Modality`, `BodyPartExamined`, `Rows`, `Columns`, `PixelSpacing`,
  `ImageOrientationPatient`, `ImagePositionPatient`, `SliceThickness`,
  `SpacingBetweenSlices`, `RescaleSlope`, `RescaleIntercept`, `PhotometricInterpretation`,
  `BitsAllocated`, `BitsStored`, `PixelRepresentation`, `BurnedInAnnotation`
- Record the **original** UIDs here, in the PHI-restricted catalog. S4 replaces them
- Missing optional tags are normal. Record absence, do not fail

**Fails on:** corrupt DICOM header, unreadable dataset

---

## S3 — Quality Gate

**In:** indexed series
**Out:** `quality_status` per series, or `quarantine` row

See `TESTING.md` for the full check list and thresholds. Summary:

- Series completeness, slice spacing uniformity, orientation consistency
- Required geometry tags present
- Rescale parameters present for CT
- Modality and body part resolvable — **unresolvable means quarantine**, because defacing
  eligibility cannot be determined

**Critical:** quarantine carries a structured reason code and the failing values. Never drop
silently, never auto-repair.

---

## S4 — De-identify

**In:** bronze instance + series quality status
**Out:** de-identified dataset in memory → S5; `deid_audit` rows

See `DICOM.md` for the full tag profile and rules. Three channels:

1. Tag profile (PS3.15 Annex E basic), deterministic UID remap, consistent date shift
2. Private tag removal (whitelist only)
3. Burned-in pixel text detection and redaction

Every action writes a `deid_audit` row: instance, tag or region, action taken, rule that
fired. This table is how you prove removal later.

**Fails on:** any tag in the must-remove list that cannot be written

---

## S5 — Verify

**In:** de-identified dataset
**Out:** pass → write to silver; fail → quarantine

**This must be separate code from S4.** A de-identifier that checks its own work proves
nothing. Independent re-scan:

- No tag from the prohibited list present with a non-empty value
- No odd-group (private) tags outside the whitelist
- All UIDs differ from originals
- Pixel OCR finds no text matching identifier patterns
- `BurnedInAnnotation` is `NO` or absent

**Any failure quarantines the instance.** Do not warn and continue.

---

## S6 — Volume Assembly

**In:** verified de-identified instances of one series
**Out:** `silver/{series_id}/volume.nii.gz` + `silver/{series_id}/meta.json`

See `DICOM.md` for the sorting and rescaling rules — **this is the stage most likely to be
silently wrong.** Sorting by `InstanceNumber` or `SliceLocation` will appear to work and
produce geometrically incorrect volumes.

- Sort by projecting `ImagePositionPatient` onto the slice normal
- Apply `RescaleSlope`/`RescaleIntercept` → Hounsfield Units (CT only)
- Resample to isotropic spacing
- Record the LPS→RAS transform in `meta.json`

**Fails on:** non-uniform spacing beyond tolerance, fewer than 3 slices, mixed orientations

---

## S7 — Deface

**In:** silver volume + body part
**Out:** defaced volume, or pass-through with a recorded decision

- **Required** when body part resolves to head, neck, sinus, face, brain, or skull
- Records a `transform_run` either way — including the explicit "not required, body part is
  chest" decision, so absence of defacing is always accounted for

**This stage is a gate, not a transform.** S9 must refuse to run if body part is
head-adjacent and no successful defacing run exists for this volume. Fail closed.

---

## S8 — Segment

**In:** defaced silver volume
**Out:** binary mask

- Threshold presets by tissue — see `GEOMETRY.md`
- Presets are **versioned parameters** recorded in `transform_runs`
- Morphological cleanup: binary closing then largest-component selection
- MR has no standardised intensity scale — HU presets do not apply. Refuse or use
  percentile-based thresholds and record which

**Fails on:** empty mask, or mask covering >90% of the volume (threshold is wrong)

---

## S9 — Surface Extraction

**In:** binary mask + spacing
**Out:** raw mesh (vertices, faces)

- `skimage.measure.marching_cubes`, spacing in array-axis order
- **Guard:** assert the defacing gate passed. See S7

**Fails on:** defacing gate not satisfied, zero faces produced

---

## S10 — Mesh Post-processing

**In:** raw mesh
**Out:** optimised mesh

Order matters — see `GEOMETRY.md`:

1. Largest connected component
2. Remove degenerate and duplicate faces
3. Taubin smoothing (not Laplacian — it shrinks)
4. Quadric edge-collapse decimation to face budget
5. Recompute normals

Record input/output face counts. Reviewers ask about the size/quality tradeoff.

---

## S11 — Export

**In:** optimised mesh, silver volume
**Out:** gold artifacts + `artifacts` rows

- `gold/{series_id}/surface.glb` — apply LPS→glTF axis conversion, or it renders rotated
- `gold/{series_id}/volume.nii.gz` — for the CV consumer
- `gold/{series_id}/thumbnail.png`
- `gold/{series_id}/manifest.json` — contract version, pipeline version, checksums

Every artifact row records checksum, size, format, and its producing `transform_run`.

---

## S12 — Serve

**In:** catalog + gold artifacts
**Out:** API responses

- Catalog search, artifact retrieval by presigned URL, lineage graph, quality report
- Artifacts are served from **gold only**. Silver and bronze are never directly servable
- Contract version is returned in every response that carries data

---

## Stage dependency graph

```
S1 -> S2 -> S3 -> S4 -> S5 -> S6 -> S7 -> S8 -> S9 -> S10 -> S11 -> S12
                   |           |      |
                   v           v      v
              deid_audit   quarantine (any stage)
```

S7 is a hard gate on S9. S5 is a hard gate on S6. Everything downstream of a quarantine
stops for that series.

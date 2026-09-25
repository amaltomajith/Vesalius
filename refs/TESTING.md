# Testing, Quality Gates & Data

Quality gate specifications, the four proof demonstrations, and where to get data.

---

# Part 1 — Quality gates (S3)

Every check has a reason code. Failures quarantine with the reason code and the actual
failing values in `detail`.

| Reason code | Check | Threshold |
|---|---|---|
| `QG_TOO_FEW_SLICES` | Instance count in series | `< 3` for volumetric modalities |
| `QG_SPACING_NONUNIFORM` | Consecutive slice position deltas | Deviation `> 1%` of median |
| `QG_SPACING_ZERO` | Median slice spacing | `== 0` (duplicate positions) |
| `QG_ORIENTATION_MIXED` | `ImageOrientationPatient` across series | Any variation `> 1e-4` |
| `QG_MISSING_GEOMETRY` | `ImagePositionPatient`, `ImageOrientationPatient`, `PixelSpacing` | Any absent |
| `QG_MISSING_RESCALE` | `RescaleSlope`/`RescaleIntercept` for CT | Absent on CT |
| `QG_PIXEL_DECODE_FAILED` | `pixel_array` access | Raises |
| `QG_DIMENSION_MISMATCH` | `Rows`/`Columns` consistent across series | Any variation |
| `QG_BODYPART_UNRESOLVED` | Body part determinable | Not in known vocabulary after inference |
| `QG_MODALITY_UNSUPPORTED` | Modality in supported set | Not in `{CT, MR}` for volumetric path |
| `QG_DUPLICATE_CONTENT` | Content hash already ingested | Exact match |
| `QG_SLICE_GAP` | Max delta vs median | `> 2×` median spacing |

**`QG_BODYPART_UNRESOLVED` is a hard failure, not a warning.** An unknown study could be a
head, and the defacing gate cannot be evaluated without knowing.

Never auto-repair. Interpolating missing slices or forcing uniform spacing produces
plausible, wrong geometry — the worst failure mode in the pipeline.

---

# Part 2 — The four proof demonstrations

These carry the grade. Build them as tests, not as manual demos.

## Proof 1 — PHI injection and removal

**Claim:** the pipeline removes identifiers across all three channels.

Take known-clean public DICOM, inject synthetic PHI, assert removal.

```python
INJECTIONS = [
    # (tag, value, channel)
    ("PatientName",             "TESTPHI^ALPHA",            "tag"),
    ("PatientID",               "PHI-000001",               "tag"),
    ("PatientBirthDate",        "19551104",                 "tag"),
    ("InstitutionName",         "TESTPHI GENERAL HOSPITAL", "tag"),
    ("ReferringPhysicianName",  "TESTPHI^BETA",             "tag"),
    ("StudyDescription",        "CT HEAD - TESTPHI^GAMMA",  "tag"),   # name in free text
    ("PatientComments",         "contact 555-0142",         "tag"),
    # private tag
    ((0x0009, 0x0010), "TESTPHI VENDOR BLOCK",              "private"),
    # burned into pixels
    ("__pixel_text__",          "TESTPHI^DELTA  MRN 88231", "pixel"),
]
```

Burn pixel text with PIL before re-encoding the pixel data.

**Assertions:**
- No injected string survives in any output tag, at any sequence depth
- No odd-group tag survives outside the whitelist
- OCR of output pixel data finds none of the injected strings
- All original UIDs are absent from output
- `deid_audit` has a row for every injected identifier

**Report as a rate:** *"N of N injected identifiers removed across M modalities."* Include
any misses — a known, documented gap is far better than an inflated claim.

## Proof 2 — Defacing gate

**Claim:** the pipeline cannot emit an identifying face mesh.

```python
def test_surface_extraction_refuses_undefaced_head():
    series = load_head_ct()
    with pytest.raises(DefacingGateError):
        extract_surface(series, skip_deface=True)

def test_defacing_recorded_for_non_head():
    series = load_chest_ct()
    run = extract_surface(series)
    assert run.deface["required"] is False
    assert run.deface["reason"] == "body_part=CHEST"
```

**Visual artifact for the report:** same head study, meshed with and without defacing, side by
side. Also show a non-facial structure (skull base, sinuses) unchanged, proving defacing did
not destroy the anatomy of interest.

## Proof 3 — Reproducibility

**Claim:** gold is disposable; lineage is real.

```python
def test_gold_rebuild_is_deterministic():
    original = {a.kind: a.sha256 for a in artifacts_for(series_id)}

    delete_gold(series_id)
    rebuild_from_silver(series_id, pipeline_version=recorded_version)

    rebuilt = {a.kind: a.sha256 for a in artifacts_for(series_id)}
    assert rebuilt == original
```

**Determinism hazards to fix before this passes:**
- Timestamps embedded in file headers → set to a fixed epoch
- Dict iteration order in JSON → `sort_keys=True`
- Non-deterministic mesh library thread scheduling → pin thread count to 1 for the test
- Random seeds anywhere → seed explicitly from the parameter set

If the hashes do not match, the pipeline has hidden state. Find it — that hunt is worth
writing up.

## Proof 4 — Contract violation

**Claim:** consumers are protected from upstream schema drift.

```python
def test_consumer_rejects_orientation_change():
    contract = load_contract("volume_contract", "1.1")   # LPS instead of RAS
    with pytest.raises(ContractViolation, match="RAS"):
        cv_consumer.load(volume, contract)
```

Left-right mirroring from an unnoticed orientation flip is a real and serious class of medical
imaging bug. Say so in the write-up — it explains why the contract exists.

---

# Part 3 — Test data

**Never use real patient DICOM.**

| Source | Use |
|---|---|
| **The Cancer Imaging Archive (TCIA)** | Primary. Large public collections of genuine clinical DICOM, already de-identified, clearly licensed |
| **LIDC-IDRI** (via TCIA) | Chest CT with nodule annotations — good for the CV consumer |
| **3D Slicer sample data** | Small, fast, convenient during development |
| **OsiriX sample datasets** | Assorted modalities including ultrasound — useful for burned-in PHI cases |
| **pydicom test files** | `pydicom.data.get_testdata_file()` — malformed and unusual files for quality gate tests |

**The useful property of TCIA data:** it is already de-identified, so it doubles as a clean
baseline *and* as the substrate for Proof 1. Inject, then verify removal.

## Fixture set to build

| Fixture | Purpose |
|---|---|
| `ct_chest_clean` | Happy path, no defacing required |
| `ct_head_clean` | Defacing required — the gate test |
| `ct_nonuniform_spacing` | `QG_SPACING_NONUNIFORM` |
| `ct_missing_slices` | `QG_SLICE_GAP` |
| `ct_mixed_orientation` | `QG_ORIENTATION_MIXED` |
| `us_burned_in_phi` | Pixel channel — ultrasound with overlay text |
| `unknown_body_part` | `QG_BODYPART_UNRESOLVED` |
| `corrupt_pixel_data` | `QG_PIXEL_DECODE_FAILED` |
| `phi_injected_*` | Generated from clean fixtures by the injection harness |

Generate the degraded fixtures **programmatically from clean ones** so they are reproducible
and can be regenerated. Do not hand-edit binaries and commit them.

---

# Part 4 — Test layout

```
tests/
  unit/
    test_deid_rules.py           tag profile, UID determinism, date shift consistency
    test_slice_ordering.py       position projection vs InstanceNumber traps
    test_hu_rescale.py           signed/unsigned, slope/intercept
    test_quality_gates.py        one test per reason code
    test_mesh_ops.py             Taubin volume preservation, decimation budget
    test_axis_conversion.py      LPS -> RAS -> glTF round trip
  integration/
    test_pipeline_e2e.py         ingest to GLB on a small fixture
    test_idempotency.py          re-run produces skip, not duplicate work
    test_quarantine_paths.py     each failure routes correctly
  proofs/
    test_phi_injection.py        Proof 1
    test_defacing_gate.py        Proof 2
    test_reproducibility.py      Proof 3
    test_contract_violation.py   Proof 4
```

The `proofs/` directory is the deliverable. Keep it separate and keep it green.

## Non-negotiable test rules

1. **Never commit real patient data**, even believed-anonymous. Fixtures come from public
   sources or are generated.
2. **Assert on geometry, not just on success.** A pipeline that runs without error and
   produces a mirrored volume is worse than one that crashes.
3. **Every quality gate has a test that triggers it.** An untested gate is a gate that does
   not work.
4. **Do not weaken an assertion to make a test pass.** If Proof 3 fails, the pipeline has
   hidden state — fix the state.

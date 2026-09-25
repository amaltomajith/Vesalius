# DICOM Reference

De-identification profile and volume assembly. **These are the two places the pipeline is
most likely to be silently wrong** — wrong in a way that produces plausible-looking output.

---

# Part 1 — De-identification

## Tag profile

Implement against DICOM PS3.15 Annex E (Basic Application Level Confidentiality Profile)
rather than an ad-hoc list. Actions: **X** remove, **Z** replace with zero-length,
**D** replace with dummy, **U** replace with a new consistent UID, **K** keep.

### Must remove or replace

| Tag | Name | Action |
|---|---|---|
| (0008,0014) | InstanceCreatorUID | U |
| (0008,0018) | SOPInstanceUID | U |
| (0008,0050) | AccessionNumber | Z |
| (0008,0080) | InstitutionName | X |
| (0008,0081) | InstitutionAddress | X |
| (0008,0090) | ReferringPhysicianName | Z |
| (0008,0092) | ReferringPhysicianAddress | X |
| (0008,0094) | ReferringPhysicianTelephoneNumbers | X |
| (0008,1010) | StationName | X |
| (0008,1030) | StudyDescription | X |
| (0008,103E) | SeriesDescription | X |
| (0008,1040) | InstitutionalDepartmentName | X |
| (0008,1048) | PhysiciansOfRecord | X |
| (0008,1050) | PerformingPhysicianName | X |
| (0008,1060) | NameOfPhysiciansReadingStudy | X |
| (0008,1070) | OperatorsName | X |
| (0008,1080) | AdmittingDiagnosesDescription | X |
| (0008,1155) | ReferencedSOPInstanceUID | U |
| (0010,0010) | PatientName | Z |
| (0010,0020) | PatientID | Z |
| (0010,0030) | PatientBirthDate | Z |
| (0010,0032) | PatientBirthTime | X |
| (0010,1000) | OtherPatientIDs | X |
| (0010,1001) | OtherPatientNames | X |
| (0010,1010) | PatientAge | X (or bucket to 5-year bands) |
| (0010,1020) | PatientSize | X |
| (0010,1030) | PatientWeight | X |
| (0010,1090) | MedicalRecordLocator | X |
| (0010,2160) | EthnicGroup | X |
| (0010,2180) | Occupation | X |
| (0010,21B0) | AdditionalPatientHistory | X |
| (0010,4000) | PatientComments | X |
| (0018,1000) | DeviceSerialNumber | X |
| (0018,1030) | ProtocolName | X |
| (0020,000D) | StudyInstanceUID | U |
| (0020,000E) | SeriesInstanceUID | U |
| (0020,0010) | StudyID | Z |
| (0020,0052) | FrameOfReferenceUID | U |
| (0020,4000) | ImageComments | X |
| (0032,1032) | RequestingPhysician | X |
| (0038,0300) | CurrentPatientLocation | X |
| (0040,0275) | RequestAttributesSequence | X |
| (0040,A730) | ContentSequence | X |

**Free-text fields are the highest risk.** `StudyDescription`, `SeriesDescription`,
`ImageComments`, and `AdditionalPatientHistory` routinely contain names typed by
technologists. Remove them rather than attempting to scrub them.

### Keep — required for the pipeline to function

`Modality` · `BodyPartExamined` · `PatientSex` · `Rows` · `Columns` · `PixelSpacing` ·
`SliceThickness` · `SpacingBetweenSlices` · `ImageOrientationPatient` ·
`ImagePositionPatient` · `RescaleSlope` · `RescaleIntercept` · `PhotometricInterpretation` ·
`BitsAllocated` · `BitsStored` · `HighBit` · `PixelRepresentation` · `SamplesPerPixel` ·
`WindowCenter` · `WindowWidth` · pixel data

`BodyPartExamined` is load-bearing — it determines defacing eligibility. If absent, infer
from `StudyDescription` **before** removing it, record the inference, then quarantine if
still unresolvable.

### Private tags

Every odd-numbered group is private and may contain anything, including identifiers.

```python
ds.remove_private_tags()   # pydicom, removes all odd-group elements
```

Whitelist-and-keep only if a specific private tag is demonstrably needed. Never blacklist.

### UID remapping — must be deterministic

Random UIDs break series grouping across re-runs and destroy idempotency. Use a keyed hash
so the same original always maps to the same replacement.

```python
import hashlib, hmac

UID_ROOT = "2.25"          # 2.25 + decimal UUID is a valid unregistered root
UID_SECRET = os.environ["DEID_UID_SECRET"]

def pseudo_uid(original: str) -> str:
    digest = hmac.new(UID_SECRET.encode(), original.encode(), hashlib.sha256).digest()
    # UID components must be numeric, no leading zeros, total length <= 64
    value = int.from_bytes(digest[:12], "big")
    return f"{UID_ROOT}.{value}"
```

Apply to every UID-valued tag, including references inside sequences.

### Date shifting

Removing dates destroys temporal analysis. Shift instead, by a constant per-patient offset.

```python
def patient_offset_days(original_patient_id: str) -> int:
    digest = hmac.new(DATE_SECRET.encode(), original_patient_id.encode(),
                      hashlib.sha256).digest()
    return -(int.from_bytes(digest[:4], "big") % 3650 + 1)   # 1-3650 days back
```

Apply the same offset to `StudyDate`, `SeriesDate`, `AcquisitionDate`, `ContentDate`.
Intervals between studies survive; absolute dates do not. Record the offset **only** in the
PHI-restricted catalog, never in the output file.

### Burned-in pixel PHI

`BurnedInAnnotation` (0028,0301) is meant to flag this. **It is frequently absent or
wrong — do not trust it.**

Highest risk: `US` (ultrasound), `SC` (secondary capture), `OT` (other), `XA`, and anything
scanned from film. CT and MR are usually clean but not guaranteed.

Detection: OCR the rendered pixel array, find text bounding boxes, redact by filling with
the background value. Text in the top ~15% of the frame is the common case (vendor overlay
banner).

```python
# Redact, then set the tag honestly
ds.BurnedInAnnotation = "NO"
```

**When OCR confidence is low, flag for review rather than auto-redacting** — over-redaction
destroys anatomy and is not recoverable.

### Sequences

PHI hides inside nested sequences. Recurse.

```python
def walk(ds):
    for elem in ds:
        if elem.VR == "SQ":
            for item in elem.value:
                walk(item)
        else:
            apply_rule(elem)
```

---

# Part 2 — Volume Assembly

## Slice ordering — do not get this wrong

**Never sort by `InstanceNumber` or `SliceLocation`.** Both are optional, both are
vendor-inconsistent, and both will produce a volume that looks plausible and is
geometrically wrong.

Sort by projecting each slice's origin onto the slice normal:

```python
import numpy as np

def slice_position(ds) -> float:
    iop = np.array(ds.ImageOrientationPatient, dtype=float)
    row_cosine, col_cosine = iop[:3], iop[3:]
    normal = np.cross(row_cosine, col_cosine)
    ipp = np.array(ds.ImagePositionPatient, dtype=float)
    return float(np.dot(ipp, normal))

slices = sorted(datasets, key=slice_position)
```

This is correct regardless of vendor, orientation, or acquisition direction.

## Spacing

Three different tags, frequently confused:

| Tag | Meaning |
|---|---|
| `PixelSpacing` (0028,0030) | **[row spacing, column spacing]** = [y, x] in mm. Note the order |
| `SliceThickness` (0018,0050) | Thickness of the imaged slab. **Not** the distance between slices |
| `SpacingBetweenSlices` (0018,0088) | Distance between slice centres. Often absent |

**Derive slice spacing from the data**, not from tags:

```python
positions = np.array([slice_position(ds) for ds in slices])
diffs = np.diff(positions)
spacing_z = float(np.median(diffs))
uniform = bool(np.allclose(diffs, spacing_z, atol=0.01 * abs(spacing_z)))
```

Slabs can overlap (`SliceThickness` > spacing) or have gaps. Both are legitimate. Non-uniform
spacing is a quality failure — quarantine rather than interpolate.

## Hounsfield Units

CT stored values are not HU. Apply the rescale:

```python
hu = stored.astype(np.float32) * float(ds.RescaleSlope) + float(ds.RescaleIntercept)
```

Typical CT: `RescaleIntercept = -1024`, `RescaleSlope = 1`.

Also honour `PixelRepresentation` (0028,0103) — `1` means signed two's complement. Decoding
signed data as unsigned gives wildly wrong values.

**MR has no standardised intensity scale.** HU thresholds are meaningless for MR. Either
refuse MR for threshold-based segmentation or use percentile-based thresholds and record
which was used.

## Coordinate systems

| System | Convention |
|---|---|
| DICOM | **LPS** — +x Left, +y Posterior, +z Superior |
| NIfTI | **RAS** — +x Right, +y Anterior, +z Superior |
| glTF | **Y-up, right-handed**, metres |

LPS → RAS is a sign flip on the first two axes. Build the affine from
`ImageOrientationPatient` and `ImagePositionPatient` rather than assuming an identity
orientation:

```python
def affine_lps(first_slice, spacing_z):
    iop = np.array(first_slice.ImageOrientationPatient, dtype=float)
    row_c, col_c = iop[:3], iop[3:]
    normal = np.cross(row_c, col_c)
    dy, dx = map(float, first_slice.PixelSpacing)    # [row, column]
    origin = np.array(first_slice.ImagePositionPatient, dtype=float)

    a = np.eye(4)
    a[:3, 0] = row_c * dx
    a[:3, 1] = col_c * dy
    a[:3, 2] = normal * spacing_z
    a[:3, 3] = origin
    return a

LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])
```

Store the affine in `meta.json`. Downstream stages must not re-derive it.

## Isotropic resampling

Anisotropic voxels (common: 0.7 × 0.7 × 3.0 mm) distort marching cubes output badly. Resample
to isotropic before meshing. Use `SimpleITK` with linear interpolation for intensity volumes
and nearest-neighbour for masks.

Record the target spacing as a versioned parameter — it changes output geometry, so it
belongs in `transform_runs`.

## Preferred libraries

- `pydicom` — tag access and de-identification. Full control, which S4 needs
- `SimpleITK` — series reading, resampling, NIfTI writing. **Do not hand-roll resampling**
- `dicom2nifti` — convenient, but less control. Acceptable for S6 if the quality gate
  already validated geometry

Do not use SimpleITK's series reader for de-identification — it abstracts away the tags you
need to inspect.

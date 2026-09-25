"""Stage-level exceptions. Raising one of these from a stage's service function is how
`run_stage` (see catalog/idempotency.py) learns to record a failed transform_run and,
where applicable, write a quarantine row.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for all stage-raised errors."""


class QuarantineError(PipelineError):
    """A subject failed validation and must be quarantined, never silently dropped.

    `reason_code` and `detail` are written verbatim into the `quarantine` table.
    """

    def __init__(self, reason_code: str, detail: dict[str, object], message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.detail = detail


class UnreadableArchiveError(QuarantineError):
    """Archive could not be opened (corrupt zip/tar, unreadable directory)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S1_UNREADABLE_ARCHIVE", detail, "archive could not be read")


class ZeroDicomFoundError(QuarantineError):
    """Archive opened fine but contained no file with a valid DICM magic."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S1_ZERO_DICOM_FOUND", detail, "no DICOM files found in archive")


class CorruptHeaderError(QuarantineError):
    """pydicom could not parse a file's header."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S2_CORRUPT_HEADER", detail, "DICOM header could not be parsed")


class UnreadableDatasetError(QuarantineError):
    """File had a valid DICM magic but the dataset could not be read at all."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S2_UNREADABLE_DATASET", detail, "DICOM dataset could not be read")


class S3UnreadableInstanceError(QuarantineError):
    """Bronze read/parse failed at S3 despite S2 having read the same content
    successfully -- an infrastructure failure (MinIO unreachable, bit rot), not a quality
    verdict. Distinct from every QG_* check, which are gate outcomes, not exceptions.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S3_UNREADABLE_INSTANCE", detail, "bronze instance could not be re-read at S3"
        )


class TagWriteFailedError(QuarantineError):
    """A must-remove/replace tag did not actually clear after S4 applied its action
    (pydicom write oddity, VR mismatch). STAGES.md: "Fails on: any tag in the must-remove
    list that cannot be written."
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S4_TAG_WRITE_FAILED", detail, "a must-remove tag could not be cleared")


class LowConfidenceBurnedInTextError(QuarantineError):
    """OCR found possible burned-in text at a confidence too low to safely auto-redact.
    DICOM.md: "When OCR confidence is low, flag for review rather than auto-redacting --
    over-redaction destroys anatomy and is not recoverable." Quarantined for human review
    rather than guessed either way, per this project's "quarantine, never drop" /
    "never auto-repair" philosophy.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S4_BURNED_IN_TEXT_LOW_CONFIDENCE",
            detail,
            "burned-in text detected at low confidence -- needs human review",
        )


class S4UnreadableInstanceError(QuarantineError):
    """Bronze read/parse failed at S4 despite S2/S3 having read the same content
    successfully -- an infrastructure failure, not a de-identification outcome.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S4_UNREADABLE_INSTANCE", detail, "bronze instance could not be re-read at S4"
        )


class ProhibitedTagPresentError(QuarantineError):
    """S5's independent rescan found a must-remove/replace tag still present with a
    non-empty value after S4 claimed to have de-identified the dataset.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S5_PROHIBITED_TAG_PRESENT", detail, "a prohibited tag is still present")


class PrivateTagPresentError(QuarantineError):
    """S5's independent rescan found an odd-group (private) tag outside the whitelist."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S5_PRIVATE_TAG_PRESENT", detail, "a private tag outside the whitelist is present"
        )


class UidNotRemappedError(QuarantineError):
    """A UID that should have been remapped still matches the original bronze value."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S5_UID_NOT_REMAPPED", detail, "a UID was not remapped from its original")


class BurnedInTextDetectedError(QuarantineError):
    """S5's independent, stricter OCR pass found text that survived S4's redaction."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S5_BURNED_IN_TEXT_DETECTED", detail, "burned-in text survived de-identification"
        )


class BurnedInAnnotationMismatchError(QuarantineError):
    """BurnedInAnnotation is neither absent nor 'NO' on the de-identified dataset."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S5_BURNED_IN_ANNOTATION_MISMATCH",
            detail,
            "BurnedInAnnotation is not 'NO' or absent",
        )


class S6UnreadableInstanceError(QuarantineError):
    """Silver read/parse failed at S6 despite S5 having written it successfully -- an
    infrastructure failure, not a geometry outcome.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S6_UNREADABLE_INSTANCE", detail, "silver instance could not be read at S6"
        )


class TooFewSlicesError(QuarantineError):
    """Fewer than 3 silver instances in the series. STAGES.md S6: "Fails on: ... fewer than
    3 slices." Re-verified independently at S6 even though S3 should already have gated
    this (CLAUDE.md rule 6: never auto-repair, and this stage over-verifies rather than
    trusts an upstream invariant blindly).
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S6_TOO_FEW_SLICES", detail, "fewer than 3 slices in series")


class OrientationMixedError(QuarantineError):
    """ImageOrientationPatient varies across silver instances beyond tolerance, re-verified
    independently at S6. STAGES.md S6: "Fails on: ... mixed orientations."
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S6_ORIENTATION_MIXED", detail, "orientation varies across instances")


class GeometryDriftError(QuarantineError):
    """The slice spacing/uniformity freshly computed from the actual silver bytes disagrees
    with S3's persisted series.slice_spacing/spacing_uniform. Should never happen given S4's
    KEEP_TAGS never touching geometry tags -- but this stage's whole premise is that an
    invariant assumed to always hold is exactly the failure mode to guard against.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S6_GEOMETRY_DRIFT",
            detail,
            "freshly-computed slice geometry disagrees with S3's persisted cache",
        )


class SpacingNonUniformError(QuarantineError):
    """Freshly-computed slice spacing is non-uniform (or zero). STAGES.md S6: "Fails on:
    non-uniform spacing beyond tolerance." Resampling would otherwise treat the MEDIAN
    spacing as if it held everywhere, silently producing plausible-but-wrong geometry --
    CLAUDE.md rule 6 forbids exactly that (never force uniform spacing). Independent of
    S3's own QG_SPACING_* gate, which should already have quarantined such a series.
    """

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S6_SPACING_NONUNIFORM", detail, "slice spacing is not uniform in the silver instances"
        )


# --- S7 (deface) ----------------------------------------------------------------------


class DefaceModalityUnsupportedError(QuarantineError):
    """Defacing is required but the modality has no supported algorithm (MR: HU thresholds are
    meaningless and bone is dark). Fail closed: the series must never reach S9 un-defaced."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_MODALITY_UNSUPPORTED", detail, "no defacing algorithm for modality")


class DefaceAffineInvalidError(QuarantineError):
    """meta.json's affine is not orthonormal / 1mm isotropic, so world-space anterior cannot be
    located reliably."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_AFFINE_INVALID", detail, "affine unusable for defacing")


class DefaceAffineMismatchError(QuarantineError):
    """The affine recorded in meta.json disagrees with the sform in the volume's own header."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_AFFINE_MISMATCH", detail, "meta.json affine != volume header sform")


class NoHeadFoundError(QuarantineError):
    """Defacing was required but no plausible head was found -- never passed through."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_NO_HEAD_FOUND", detail, "no plausible head found in volume")


class DefaceNoEffectError(QuarantineError):
    """Defacing was required and ran, but changed (almost) nothing."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_NO_EFFECT", detail, "defacing changed too few voxels")


class DefaceChangeImplausibleError(QuarantineError):
    """Defacing removed an implausibly large share of the body."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_CHANGE_IMPLAUSIBLE", detail, "defacing changed too many voxels")


class DefaceEfficacyCheckFailedError(QuarantineError):
    """The independent verifier rejected the defaced output (invariant or efficacy)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_EFFICACY_CHECK_FAILED", detail, "independent defacing check failed")


class DefaceOutputHeaderMismatchError(QuarantineError):
    """The defaced NIfTI's header differs from the source's -- a real quarantine, unlike S6's
    bare assert, so it surfaces in the catalog."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_OUTPUT_HEADER_MISMATCH", detail, "defaced header != source header")


class DefaceInputTamperedError(QuarantineError):
    """The S6 volume/meta bytes in silver do not hash to what the catalog recorded."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_INPUT_TAMPERED", detail, "silver input differs from catalog hash")


class DefaceUnreadableInputError(QuarantineError):
    """Infrastructure failure reading the S6 volume/meta (S6's precedent: quarantine)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S7_UNREADABLE_INPUT", detail, "S6 volume could not be read at S7")


class DefacingGateError(PipelineError):
    """S8/S9/S11 asked for a volume and the defacing gate refused (CLAUDE.md rule 4: refuse,
    do not warn). Deliberately NOT a QuarantineError: a refusal says the request cannot be
    served, not that the series is defective. `reason_code` is a stable GATE_* string.
    """

    def __init__(self, reason_code: str, detail: dict[str, object]) -> None:
        super().__init__(f"defacing gate refused: {reason_code}")
        self.reason_code = reason_code
        self.detail = detail


# --- S8 (segment) -----------------------------------------------------------------------------


class SegmentModalityUnsupportedError(QuarantineError):
    """A preset was requested on a non-CT volume. HU thresholds are meaningless on MR (STAGES.md
    S8): refuse rather than emit a mask with no tissue meaning."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_MODALITY_UNSUPPORTED", detail, "no HU-based segmentation for modality")


class EmptyMaskError(QuarantineError):
    """The threshold produced no voxels, or cleanup removed every voxel -- the threshold is
    wrong for this study, not a study defect to route around (GEOMETRY.md, STAGES.md S8)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_EMPTY_MASK", detail, "segmentation produced an empty mask")


class MaskTooLargeError(QuarantineError):
    """The mask covers more than 90% of the volume -- the threshold is wrong (STAGES.md S8)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_MASK_TOO_LARGE", detail, "mask covers implausibly much of the volume")


class SegmentGeometryMismatchError(QuarantineError):
    """The gated volume's shape/affine disagree with its own meta.json."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_GEOMETRY_MISMATCH", detail, "volume shape/affine != meta.json")


class SegmentUnreadableInputError(QuarantineError):
    """Infrastructure failure reading the gated volume/meta (S6/S7 precedent: quarantine)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_UNREADABLE_INPUT", detail, "gated volume could not be read at S8")


class SegmentOutputHeaderMismatchError(QuarantineError):
    """The mask NIfTI's geometry does not match the source volume's."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S8_OUTPUT_HEADER_MISMATCH", detail, "mask header != source header")


# --- S9 (surface extraction) -------------------------------------------------------------------


class SurfaceModalityUnsupportedError(QuarantineError):
    """Marching cubes needs a meaningful intensity level; MR has no standardised scale."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_MODALITY_UNSUPPORTED", detail, "no HU-based surface for modality")


class SurfaceGeometryMismatchError(QuarantineError):
    """The S8 mask's shape/affine disagree with the gated volume's own."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_GEOMETRY_MISMATCH", detail, "mask shape/affine != volume")


class SurfaceAffineInvalidError(QuarantineError):
    """meta.json's affine is not orthonormal / isotropic, so world-space vertex placement (and
    the confinement dilation, which assumes isotropic voxels) cannot be trusted."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_AFFINE_INVALID", detail, "affine unusable for surface extraction")


class SurfaceInvariantViolationError(QuarantineError):
    """The extracted mesh violates its own postconditions (a vertex outside the volume's world
    bounding box, or a face index out of range) -- an internal bug, never passed through."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_INVARIANT_VIOLATION", detail, "extracted mesh failed a postcondition")


class SurfaceUnreadableInputError(QuarantineError):
    """Infrastructure failure reading the gated volume or the S8 mask (S6-S8 precedent)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_UNREADABLE_INPUT", detail, "input could not be read at S9")


class SurfaceInputTamperedError(QuarantineError):
    """The S8 mask bytes in silver do not hash to what the catalog recorded."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_INPUT_TAMPERED", detail, "silver mask differs from catalog hash")


class SurfaceZeroFacesError(QuarantineError):
    """Marching cubes produced no surface at all (STAGES.md S9: "Fails on: ... zero faces")."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_ZERO_FACES", detail, "marching cubes produced zero faces")


class SurfaceOutputMismatchError(QuarantineError):
    """The persisted mesh does not match the in-memory result once re-read."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S9_OUTPUT_MISMATCH", detail, "persisted mesh != in-memory mesh")


# --- S10 (mesh post-processing) ------------------------------------------------------------


class MeshUnreadableInputError(QuarantineError):
    """Infrastructure failure reading S9's raw mesh (S7-S9 precedent: quarantine)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S10_UNREADABLE_INPUT", detail, "raw mesh could not be read at S10")


class MeshInputTamperedError(QuarantineError):
    """The S9 mesh bytes in silver do not hash to what the catalog recorded."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S10_INPUT_TAMPERED", detail, "silver mesh differs from catalog hash")


class MeshZeroFacesError(QuarantineError):
    """Post-processing left zero faces (e.g. every face turned out degenerate)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S10_ZERO_FACES", detail, "mesh post-processing produced zero faces")


class MeshOutputMismatchError(QuarantineError):
    """The persisted optimised mesh does not match the in-memory result once re-read."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S10_OUTPUT_MISMATCH", detail, "persisted mesh != in-memory mesh")


class MeshPostconditionError(QuarantineError):
    """The post-processed mesh violates its own postconditions (e.g. a face index out of
    range after a mutating trimesh call) -- an internal bug, never passed through."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S10_POSTCONDITION_FAILED", detail, "post-processed mesh failed a postcondition"
        )


# --- S11 (export) -----------------------------------------------------------------------------


class ExportUnreadableInputError(QuarantineError):
    """Infrastructure failure reading S10's optimised mesh (S7-S10 precedent: quarantine)."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S11_UNREADABLE_INPUT", detail, "optimised mesh could not be read at S11")


class ExportInputTamperedError(QuarantineError):
    """The S10 mesh bytes in silver do not hash to what the catalog recorded."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__("S11_INPUT_TAMPERED", detail, "silver mesh differs from catalog hash")


class ExportOutputMismatchError(QuarantineError):
    """A persisted gold artifact (GLB, volume copy, or thumbnail) does not match the in-memory
    result once re-read."""

    def __init__(self, detail: dict[str, object]) -> None:
        super().__init__(
            "S11_OUTPUT_MISMATCH", detail, "persisted gold artifact != in-memory result"
        )

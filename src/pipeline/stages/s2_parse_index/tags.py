"""Tag extraction for S2. Per refs/STAGES.md S2, plus SeriesInstanceUID/StudyInstanceUID
(needed for grouping) and FrameOfReferenceUID (needed by
`restricted.uid_map.uid_type='frame_of_reference'`, present in the schema but omitted
from STAGES.md's list -- captured here since it costs nothing alongside tags already
being read).

Missing optional tags are recorded as absent (None), never a failure -- STAGES.md is
explicit about this.
"""

from __future__ import annotations

from typing import Any

import pydicom

from pipeline.common.body_part import BodyPartClass, classify_body_part

_SCALAR_TAGS = (
    "Modality",
    "BodyPartExamined",
    "Rows",
    "Columns",
    "SliceThickness",
    "SpacingBetweenSlices",
    "RescaleSlope",
    "RescaleIntercept",
    "PhotometricInterpretation",
    "BitsAllocated",
    "BitsStored",
    "PixelRepresentation",
    "BurnedInAnnotation",
    "SOPInstanceUID",
    "SeriesInstanceUID",
    "StudyInstanceUID",
    "FrameOfReferenceUID",
    "PatientID",
    # Needed only for S3's body-part inference fallback when BodyPartExamined is absent
    # (DICOM.md's inference step, consolidated into S3 -- see refs/DICOM.md and the S3
    # plan). Free text, highest PHI risk of any tag (DICOM.md) -- S3 must never persist
    # the raw value anywhere, only a matched keyword or None.
    "StudyDescription",
)

_MULTI_VALUE_TAGS = ("PixelSpacing", "ImageOrientationPatient", "ImagePositionPatient")


def extract_tags(ds: pydicom.dataset.Dataset) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in _SCALAR_TAGS:
        raw = getattr(ds, name, None)
        values[name] = str(raw) if raw is not None else None

    for name in _MULTI_VALUE_TAGS:
        raw = getattr(ds, name, None)
        values[name] = [float(x) for x in raw] if raw is not None else None

    return values


def resolve_deface_required(body_part: str | None) -> tuple[bool, str, str | None]:
    """Returns (deface_required, body_part_source, normalized_body_part).

    Delegates to the closed vocabulary in `common/body_part.py`. `deface_required` is False
    ONLY for a positively-recognised non-head term. A present-but-unrecognised or ambiguous
    value is fail-closed: required, and `body_part_source="unresolved"` -- so S3 both tries to
    rescue it from the study description and quarantines it if that fails. (Previously any
    non-empty value outside the head list, e.g. `SINUSES` or `XYZ`, was treated as resolved
    and not-a-head, which let a real face CT skip defacing.)

    The returned/stored value stays the plain strip+upper form, as before.
    """
    cls = classify_body_part(body_part)
    if cls is BodyPartClass.ABSENT:
        return True, "unresolved", None
    normalized = str(body_part).strip().upper()
    if cls is BodyPartClass.HEAD_ADJACENT:
        return True, "tag", normalized
    if cls is BodyPartClass.KNOWN_NON_HEAD:
        return False, "tag", normalized
    return True, "unresolved", normalized

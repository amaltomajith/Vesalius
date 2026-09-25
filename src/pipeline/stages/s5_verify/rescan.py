"""S5's independent prohibited-tag / private-tag / UID scan. Deliberately does NOT import
anything from s4_deidentify (no tagwalk, no rules, no private_tags) -- a de-identifier that
verifies itself proves nothing (KICKOFF-PROMPT.md). `_rescan_walk` below is a separately
written traversal, logically similar to s4_deidentify/tagwalk.py's walk() but not the same
code, so a bug specific to S4's traversal wouldn't be silently inherited by its own check.

What IS shared with S4 is data, not logic: common/deid_profile.py's TAG_PROFILE_BY_TAG /
PRIVATE_TAG_WHITELIST / UID_REMAP_TAG_NAMES are the single canonical list both stages read
-- there must be exactly one, or the two could silently drift and both be "consistently
wrong" together.
"""

from __future__ import annotations

from collections.abc import Callable

import pydicom
from pydicom.dataelem import DataElement

from pipeline.common.deid_profile import (
    PRIVATE_TAG_WHITELIST,
    TAG_PROFILE_BY_TAG,
    UID_REMAP_TAG_NAMES,
)
from pipeline.stages.s5_verify.models import VerifyFailure


def _rescan_walk(
    ds: pydicom.dataset.Dataset, visit: Callable[[pydicom.dataset.Dataset, DataElement], None]
) -> None:
    for elem in list(ds):
        if elem.VR == "SQ":
            for item in elem.value:
                _rescan_walk(item, visit)
        visit(ds, elem)


def _tag_str(elem: DataElement) -> str:
    return f"({elem.tag.group:04X},{elem.tag.element:04X})"


def check_prohibited_tags_absent(ds: pydicom.dataset.Dataset) -> VerifyFailure | None:
    offenders: list[dict[str, object]] = []

    def _visit(parent: pydicom.dataset.Dataset, elem: DataElement) -> None:
        rule = TAG_PROFILE_BY_TAG.get((elem.tag.group, elem.tag.element))
        if rule is None or rule.action == "U":
            return  # U-action tags are checked separately (they SHOULD have a value)
        if rule.action == "X":
            offenders.append(
                {
                    "tag": _tag_str(elem),
                    "tag_name": rule.name,
                    "action": "X",
                    "reason": "still present",
                }
            )
        elif rule.action in ("Z", "D") and elem.value not in (None, "", []):
            offenders.append(
                {
                    "tag": _tag_str(elem),
                    "tag_name": rule.name,
                    "action": rule.action,
                    "reason": "non-empty value",
                }
            )

    _rescan_walk(ds, _visit)
    if offenders:
        return VerifyFailure("S5_PROHIBITED_TAG_PRESENT", {"offenders": offenders})
    return None


def check_no_private_tags_outside_whitelist(ds: pydicom.dataset.Dataset) -> VerifyFailure | None:
    offenders: list[dict[str, object]] = []

    def _visit(parent: pydicom.dataset.Dataset, elem: DataElement) -> None:
        if not elem.tag.is_private:
            return
        if (elem.tag.group, elem.tag.element) in PRIVATE_TAG_WHITELIST:
            return
        offenders.append({"tag": _tag_str(elem), "tag_name": elem.keyword or None})

    _rescan_walk(ds, _visit)
    if offenders:
        return VerifyFailure("S5_PRIVATE_TAG_PRESENT", {"offenders": offenders})
    return None


def check_uids_differ_from_original(
    ds: pydicom.dataset.Dataset, original_ds: pydicom.dataset.Dataset
) -> VerifyFailure | None:
    """Top-level identity UIDs only (Study/Series/SOP/FrameOfReference) -- the ones
    STAGES.md's "All UIDs differ from originals" is centrally concerned with, and the ones
    virtually every DICOM instance carries at the top level (unlike ReferencedSOPInstanceUID,
    typically nested under a ReferencedImageSequence, which is out of scope for this check).
    """
    offenders: list[dict[str, object]] = []
    for name in sorted(UID_REMAP_TAG_NAMES):
        original_val = getattr(original_ds, name, None)
        if original_val is None:
            continue
        deid_val = getattr(ds, name, None)
        if deid_val is None:
            offenders.append({"tag_name": name, "reason": "missing after de-identification"})
        elif str(deid_val) == str(original_val):
            offenders.append({"tag_name": name, "reason": "unchanged from original"})

    if offenders:
        return VerifyFailure("S5_UID_NOT_REMAPPED", {"offenders": offenders})
    return None


def check_burned_in_annotation(ds: pydicom.dataset.Dataset) -> VerifyFailure | None:
    value = getattr(ds, "BurnedInAnnotation", None)
    if value is not None and str(value).strip().upper() != "NO":
        return VerifyFailure("S5_BURNED_IN_ANNOTATION_MISMATCH", {"value": str(value)})
    return None

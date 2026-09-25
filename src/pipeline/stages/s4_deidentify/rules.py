"""S4 channel 1 -- the tag profile (PS3.15 Annex E basic), deterministic UID remap,
consistent per-patient date shift. Pure and deterministic: given the same dataset bytes
and the same secret, produces byte-identical output every call -- no DB access here, so
this is safely recomputed on every S4 run without needing to be reconstructed from a
database row (see s4_deidentify/service.py's idempotency design).
"""

from __future__ import annotations

import pydicom
from pydicom.dataelem import DataElement

from pipeline.common.deid_crypto import pseudo_uid, shift_date
from pipeline.common.deid_profile import (
    DATE_TAGS_TO_SHIFT,
    PROFILE_VERSION,
    TAG_PROFILE_BY_TAG,
    UID_REMAP_TAG_NAMES,
    TagRule,
)
from pipeline.common.errors import TagWriteFailedError
from pipeline.stages.s4_deidentify.models import DeidActionRecord
from pipeline.stages.s4_deidentify.tagwalk import walk


def _format_tag(elem: DataElement) -> str:
    return f"({elem.tag.group:04X},{elem.tag.element:04X})"


def _apply_rule(
    parent: pydicom.dataset.Dataset, elem: DataElement, rule: TagRule, uid_secret: str
) -> DeidActionRecord:
    tag_str = _format_tag(elem)
    rule_id = f"profile-v{PROFILE_VERSION}:{tag_str}"

    if rule.action == "X":
        del parent[elem.tag]
        if rule.name in parent:
            raise TagWriteFailedError(
                {"tag": tag_str, "tag_name": rule.name, "action": "X", "reason": "still present"}
            )
        return DeidActionRecord(
            channel="tag", action="remove", rule_id=rule_id, tag=tag_str, tag_name=rule.name
        )

    if rule.action == "Z":
        parent[elem.tag].value = ""
        if parent[elem.tag].value not in ("", None, []):
            raise TagWriteFailedError(
                {"tag": tag_str, "tag_name": rule.name, "action": "Z", "reason": "not cleared"}
            )
        return DeidActionRecord(
            channel="tag", action="replace", rule_id=rule_id, tag=tag_str, tag_name=rule.name
        )

    if rule.action == "U":
        if rule.name not in UID_REMAP_TAG_NAMES:
            # Defensive: TAG_PROFILE currently only assigns "U" to names that are also in
            # the allowlist, but this guards against the two ever silently drifting apart.
            raise TagWriteFailedError(
                {
                    "tag": tag_str,
                    "tag_name": rule.name,
                    "action": "U",
                    "reason": "not in UID_REMAP_TAG_NAMES allowlist",
                }
            )
        original = str(elem.value)
        remapped = pseudo_uid(original, uid_secret)
        parent[elem.tag].value = remapped
        if str(parent[elem.tag].value) != remapped or str(parent[elem.tag].value) == original:
            raise TagWriteFailedError(
                {"tag": tag_str, "tag_name": rule.name, "action": "U", "reason": "UID not remapped"}
            )
        return DeidActionRecord(
            channel="tag", action="uid_remap", rule_id=rule_id, tag=tag_str, tag_name=rule.name
        )

    raise TagWriteFailedError(
        {"tag": tag_str, "tag_name": rule.name, "action": rule.action, "reason": "unhandled action"}
    )


def _shift_date_element(elem: DataElement, offset_days: int) -> DeidActionRecord | None:
    raw = str(elem.value).strip() if elem.value else ""
    if not raw:
        return None
    try:
        original = pydicom.valuerep.DA(raw)
    except (ValueError, TypeError):
        return None
    shifted = shift_date(original, offset_days)
    elem.value = shifted.strftime("%Y%m%d")
    tag_str = f"({elem.tag.group:04X},{elem.tag.element:04X})"
    return DeidActionRecord(
        channel="tag",
        action="date_shift",
        rule_id=f"date-shift-v{PROFILE_VERSION}:{elem.keyword}",
        tag=tag_str,
        tag_name=elem.keyword,
    )


def apply_tag_profile(
    ds: pydicom.dataset.Dataset, *, uid_secret: str, date_offset_days: int
) -> list[DeidActionRecord]:
    """Mutates `ds` in place. Records one DeidActionRecord per action taken (STAGES.md:
    "this table is how you prove removal later"). Recurses into sequences via tagwalk.walk,
    which is how "apply to every UID-valued tag, including references inside sequences"
    (DICOM.md) is satisfied -- the same tag-number lookup applies uniformly at every
    nesting depth walk() visits, not just the top level.
    """
    records: list[DeidActionRecord] = []

    def _visit(parent: pydicom.dataset.Dataset, elem: DataElement) -> None:
        tag_tuple = (elem.tag.group, elem.tag.element)
        rule = TAG_PROFILE_BY_TAG.get(tag_tuple)
        if rule is not None:
            records.append(_apply_rule(parent, elem, rule, uid_secret))
            return
        if elem.keyword in DATE_TAGS_TO_SHIFT:
            record = _shift_date_element(elem, date_offset_days)
            if record is not None:
                records.append(record)

    walk(ds, _visit)
    return records


__all__ = ["apply_tag_profile"]

"""S4 channel 2 -- private tag removal. DICOM.md: "Every odd-numbered group is private
and may contain anything, including identifiers... whitelist-and-keep only if a specific
private tag is demonstrably needed. Never blacklist."

Uses this package's own tagwalk.walk() (already recurses into sequences) rather than
pydicom's built-in `Dataset.remove_private_tags()` so that one pass both finds and removes
private elements while also producing an audit record per tag -- STAGES.md: "this table is
how you prove removal later," which the built-in (silent, no return value) can't satisfy on
its own.
"""

from __future__ import annotations

import pydicom
from pydicom.dataelem import DataElement

from pipeline.common.deid_profile import PRIVATE_TAG_WHITELIST, PRIVATE_TAG_WHITELIST_VERSION
from pipeline.stages.s4_deidentify.models import DeidActionRecord
from pipeline.stages.s4_deidentify.tagwalk import walk


def apply_private_tag_removal(ds: pydicom.dataset.Dataset) -> list[DeidActionRecord]:
    records: list[DeidActionRecord] = []

    def _visit(parent: pydicom.dataset.Dataset, elem: DataElement) -> None:
        if not elem.tag.is_private:
            return
        tag_tuple = (elem.tag.group, elem.tag.element)
        if tag_tuple in PRIVATE_TAG_WHITELIST:
            return
        tag_str = f"({elem.tag.group:04X},{elem.tag.element:04X})"
        tag_name = elem.keyword or None
        del parent[elem.tag]
        records.append(
            DeidActionRecord(
                channel="private",
                action="remove",
                rule_id=f"private-whitelist-v{PRIVATE_TAG_WHITELIST_VERSION}",
                tag=tag_str,
                tag_name=tag_name,
            )
        )

    walk(ds, _visit)
    return records

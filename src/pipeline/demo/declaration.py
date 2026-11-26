"""A person-stated body part for uploaded scans whose files carry no usable label.

Most real DICOM leaves the body-part tag empty, and S3 (correctly) refuses any scan whose body
part is unknown, because an unlabelled head must never skip face removal. This lets the person
who uploads say what the scan shows, under strict rules that only ever move toward MORE defacing:

  * it applies only to studies S2 left "unresolved" -- a label the files carry always wins;
  * it is stored with source "declared", so the catalog shows it was stated by a person and not
    read from the files;
  * a non-head declaration is refused (the study stays unresolved, and S3 reasons about it) when
    the study description positively names a head-adjacent part -- the person cannot talk the
    pipeline out of defacing a scan that says it is a head;
  * only a closed list can be declared, and it maps onto the shared vocabulary, so S7 decides
    exactly as it would for a labelled file (its series-header check still runs).

The study description is read only long enough to classify it and is never stored or logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.models import Instance, Series, Study
from pipeline.common.body_part import BodyPartClass, classify_body_part, infer_from_description

AUTO = "auto"
SOURCE = "declared"
# what the person may say -> the vocabulary term recorded
DECLARABLE: dict[str, str] = {"head": "HEAD", "chest": "CHEST", "abdomen": "ABDOMEN"}
LABELS: dict[str, str] = {
    "head": "a head scan",
    "chest": "a chest scan",
    "abdomen": "an abdomen scan",
}


@dataclass(frozen=True)
class Applied:
    applied: int = 0  # studies given the stated body part
    refused_head_in_description: int = 0  # non-head statement overruled: the files say head
    already_labelled: int = 0  # studies whose files carry a label, which wins

    def note(self, choice: str) -> str:
        said = LABELS.get(choice, choice)
        parts: list[str] = []
        if self.applied:
            parts.append(f"The files had no body-part label, so your statement ({said}) was used.")
        if self.refused_head_in_description:
            parts.append(
                "You said this is not a head scan, but the files describe a head, so your "
                "statement was not used (a head is never treated as anything else)."
            )
        if self.already_labelled and not self.applied:
            parts.append("The files carry their own body-part label, so it was used instead.")
        return " ".join(parts)


def is_valid(choice: str) -> bool:
    return choice == AUTO or choice in DECLARABLE


def apply_declaration(
    session: Session,
    instance_ids: Iterable[UUID],
    choice: str,
    *,
    read_description: Callable[[Instance], str | None],
) -> Applied:
    """Stamp `choice` onto the unresolved studies of these instances. Never raises on a bad
    choice: an unknown one applies nothing (the caller validates first)."""
    if choice not in DECLARABLE:
        return Applied()
    value = DECLARABLE[choice]
    non_head = classify_body_part(value) is BodyPartClass.KNOWN_NON_HEAD

    instances = list(
        session.execute(select(Instance).where(Instance.id.in_(list(instance_ids)))).scalars()
    )
    series_ids = {i.series_id for i in instances}
    studies: dict[UUID, Study] = {}
    sample: dict[UUID, Instance] = {}
    for series in session.execute(select(Series).where(Series.id.in_(series_ids))).scalars():
        studies[series.study.id] = series.study
        sample.setdefault(series.study.id, next(i for i in instances if i.series_id == series.id))

    applied = refused = labelled = 0
    for study_id, study in studies.items():
        # "declared" is a person's earlier statement about these same files (a retry): it may be
        # restated. A label the FILES carry ("tag", "inferred") is never overwritten.
        if study.body_part_source not in ("unresolved", SOURCE):
            labelled += 1
            continue
        if non_head:
            inferred = infer_from_description(read_description(sample[study_id]))
            if classify_body_part(inferred) is BodyPartClass.HEAD_ADJACENT:
                refused += 1
                continue
        study.body_part = value
        study.body_part_source = SOURCE
        study.deface_required = classify_body_part(value) is not BodyPartClass.KNOWN_NON_HEAD
        applied += 1
    session.commit()
    return Applied(applied, refused, labelled)

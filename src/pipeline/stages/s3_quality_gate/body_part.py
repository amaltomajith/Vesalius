"""Body-part inference from free-text StudyDescription, for series whose BodyPartExamined
tag was absent or unrecognised at S2 (`study.body_part_source == "unresolved"`).

The vocabulary and the matching live in `common/body_part.py` (shared with S2 and S7); this
module is the S3-facing name for it. Matching is whole-token against a closed vocabulary --
never substring -- so noise can never conclude "not a head".

Hard constraint: the raw StudyDescription text must never be persisted anywhere in the
catalog (quarantine.detail, transform_runs.parameters, logs) -- DICOM.md calls free-text
fields the highest PHI risk of any tag. Only this module's return value (a matched vocabulary
term, or None) may ever reach the catalog.
"""

from __future__ import annotations

from pipeline.common.body_part import infer_from_description


def infer_body_part_from_text(study_description: str | None) -> str | None:
    """Returns a normalised vocabulary term, or None if nothing recognisable is found.
    The caller is responsible for never persisting `study_description` itself.
    """
    return infer_from_description(study_description)

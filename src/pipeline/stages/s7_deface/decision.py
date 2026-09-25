"""Does this series need defacing? Pure and deterministic -- no DB, no I/O.

Fail-closed by construction: "not required" is concluded ONLY when every piece of evidence
positively says known-non-head. Anything else -- head-adjacent, ambiguous, unrecognised,
absent, an unresolved source, a stale flag that still says True, or a series header that
disagrees -- means required. Absence of defacing must always be explained, so every branch
yields a non-empty `reason`.

Evidence is deliberately redundant (S7 plan section 2.3):
  1. the study's body part -- but that is study-level and first-writer-wins in S2, so a
     CHEST series and a HEAD series sharing a study store body_part='CHEST';
  2. the cached `studies.deface_required` flag -- a monotone OR, but possibly computed under
     the old open vocabulary;
  3. the series' own silver headers (BodyPartExamined survives S4: it is in KEEP_TAGS).
The flag and headers can only ever push toward "required", never away from it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pipeline.common.body_part import (
    VOCAB_VERSION,
    BodyPartClass,
    classify_body_part,
    normalise,
)
from pipeline.stages.s7_deface.params import METHOD

_MAX_TERM_LEN = 32  # BodyPartExamined is a CS defined term (<= 16 chars); clamp regardless


def _clip(value: str | None) -> str | None:
    return None if value is None else value[:_MAX_TERM_LEN]


@dataclass(frozen=True)
class DefaceDecision:
    required: bool
    method: str  # METHOD when required, "none" otherwise
    reason: str  # never empty
    basis: str
    vocab_version: str
    evidence: dict[str, object] = field(default_factory=dict)

    def to_block(self) -> dict[str, object]:
        """The `deface` block recorded in transform_runs.parameters (TESTING.md Proof 2 reads
        `["required"]` and `["reason"]`)."""
        return {
            "required": self.required,
            "method": self.method,
            "reason": self.reason,
            "basis": self.basis,
        }


def decide_deface(
    *,
    study_body_part: str | None,
    study_body_part_source: str | None,
    study_deface_required_flag: bool | None,
    series_header_body_parts: Sequence[str | None],
) -> DefaceDecision:
    study_cls = classify_body_part(study_body_part)
    study_value = _clip(None if study_body_part is None else study_body_part.strip().upper())
    present_headers = [h for h in series_header_body_parts if h is not None and normalise(h)]
    header_classes = [classify_body_part(h) for h in present_headers]

    reasons: list[str] = []
    bases: list[str] = []
    mismatch: list[str] = []

    if study_cls is BodyPartClass.HEAD_ADJACENT:
        reasons.append(f"body_part={study_value}")
        bases.append("head_adjacent")
    elif study_cls is BodyPartClass.UNRECOGNISED:
        reasons.append(f"body_part_unrecognised={study_value}")
        bases.append("unrecognised")
    elif study_cls is BodyPartClass.AMBIGUOUS:
        reasons.append(f"body_part_ambiguous={study_value}")
        bases.append("ambiguous")
    elif study_cls is BodyPartClass.ABSENT:
        reasons.append("body_part_absent")
        bases.append("absent")
    elif study_body_part_source == "unresolved":
        # a value that looks known but was never positively resolved -- do not trust it
        reasons.append("body_part_source_unresolved")
        bases.append("source_unresolved")

    if study_cls is BodyPartClass.KNOWN_NON_HEAD and study_deface_required_flag is not False:
        # the rule is `flag is False`: True is a mismatch worth recording, None is unknown
        if study_deface_required_flag:
            reasons.append(f"study_deface_required_flag=true (body_part={study_value})")
            mismatch.append("flag_true_recomputed_false")
        else:
            reasons.append("study_deface_required_flag_unknown")
        bases.append("flag")
    if study_cls is not BodyPartClass.KNOWN_NON_HEAD and study_deface_required_flag is False:
        mismatch.append("recomputed_true_flag_false")

    for header, cls in sorted(zip(present_headers, header_classes, strict=True)):
        if cls is not BodyPartClass.KNOWN_NON_HEAD:
            reasons.append(
                f"series_header_body_part={_clip(header.strip().upper())} (study={study_value})"
            )
            bases.append("series_header")
            if study_cls is BodyPartClass.KNOWN_NON_HEAD:
                mismatch.append("header_disagrees_with_study")

    required = bool(reasons)
    if required:
        reason = "; ".join(sorted(set(reasons)))
        basis = bases[0]
        method = METHOD
    else:
        # Not required: only reachable when the study is known-non-head, the flag is not True,
        # the source is not "unresolved" and every present header is known-non-head.
        reason = f"body_part={study_value}"
        basis = "known_non_head"
        method = "none"

    evidence: dict[str, object] = {
        "study_body_part": study_value,
        "study_body_part_class": study_cls.value,
        "study_body_part_source": study_body_part_source,
        "study_deface_required_flag": study_deface_required_flag,
        "series_header_body_parts": sorted(
            {_clip(h.strip().upper()) or "" for h in present_headers}
        ),
        "mismatch": sorted(set(mismatch)),
    }
    return DefaceDecision(
        required=required,
        method=method,
        reason=reason,
        basis=basis,
        vocab_version=VOCAB_VERSION,
        evidence=evidence,
    )

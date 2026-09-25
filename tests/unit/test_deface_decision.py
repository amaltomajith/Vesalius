"""The defacing decision (S7 plan section 2.3). Fail-closed: "not required" only from positive
evidence everywhere; every branch explains itself (CLAUDE.md: absence of defacing must always
be explained)."""

from __future__ import annotations

import itertools
import json

import pytest

from pipeline.common.body_part import BodyPartClass, classify_body_part
from pipeline.stages.s7_deface.decision import decide_deface


def _decide(
    body_part: str | None = "CHEST",
    source: str | None = "tag",
    flag: bool | None = False,
    headers: tuple[str | None, ...] = (),
):
    return decide_deface(
        study_body_part=body_part,
        study_body_part_source=source,
        study_deface_required_flag=flag,
        series_header_body_parts=headers,
    )


def test_chest_is_not_required_and_the_reason_is_exactly_what_proof_2_expects() -> None:
    d = _decide("CHEST", headers=("CHEST", "CHEST"))
    assert d.required is False
    assert d.reason == "body_part=CHEST"  # TESTING.md Proof 2
    assert d.method == "none"
    assert d.basis == "known_non_head"
    assert d.to_block() == {
        "required": False,
        "method": "none",
        "reason": "body_part=CHEST",
        "basis": "known_non_head",
    }


def test_head_is_required() -> None:
    d = _decide("HEAD", flag=True)
    assert (d.required, d.method, d.reason, d.basis) == (
        True,
        "geometric_v1",
        "body_part=HEAD",
        "head_adjacent",
    )


@pytest.mark.parametrize("value", ["SINUSES", "ORBITS", "FACIAL", "CERVICAL", "HEAD_NECK", "JAW"])
def test_head_synonyms_are_required(value: str) -> None:
    assert _decide(value, flag=True).required is True


@pytest.mark.parametrize(
    "value, expected_reason",
    [
        ("XYZ", "body_part_unrecognised=XYZ"),
        ("SPINE", "body_part_ambiguous=SPINE"),
        (None, "body_part_absent"),
        ("  ", "body_part_absent"),
    ],
)
def test_unrecognised_ambiguous_and_absent_are_required(value, expected_reason) -> None:  # type: ignore[no-untyped-def]
    d = _decide(value, source="unresolved", flag=True)
    assert d.required is True
    assert d.reason == expected_reason


def test_stale_flag_true_overrides_a_known_non_head_study_and_is_recorded_as_a_mismatch() -> None:
    d = _decide("CHEST", flag=True)
    assert d.required is True
    assert d.reason == "study_deface_required_flag=true (body_part=CHEST)"
    assert "flag_true_recomputed_false" in d.evidence["mismatch"]  # type: ignore[operator]


def test_unknown_flag_is_not_treated_as_false() -> None:
    d = _decide("CHEST", flag=None)
    assert d.required is True
    assert d.reason == "study_deface_required_flag_unknown"


def test_a_known_value_whose_source_is_unresolved_is_not_trusted() -> None:
    d = _decide("CHEST", source="unresolved")
    assert d.required is True
    assert d.reason == "body_part_source_unresolved"


def test_mixed_study_first_writer_wins_is_neutralised_by_the_series_header() -> None:
    """S2 stores body_part='CHEST' (first writer) for a study that also holds a HEAD series,
    while its deface_required flag is the monotone OR (True)."""
    d = _decide("CHEST", flag=True, headers=("HEAD",))
    assert d.required is True
    assert "series_header_body_part=HEAD (study=CHEST)" in d.reason


def test_series_header_alone_can_force_required_even_when_the_flag_is_false() -> None:
    d = _decide("CHEST", flag=False, headers=("HEAD",))
    assert d.required is True
    assert d.reason == "series_header_body_part=HEAD (study=CHEST)"
    assert d.evidence["mismatch"] == ["header_disagrees_with_study"]


def test_absent_series_headers_cannot_contradict_and_are_ignored() -> None:
    assert _decide("CHEST", headers=(None, None)).required is False
    assert _decide("CHEST", headers=("", None)).required is False


def test_an_unrecognised_series_header_is_required() -> None:
    assert _decide("CHEST", headers=("CHEST", "XYZ")).required is True


def test_head_study_with_a_false_flag_records_the_stale_flag() -> None:
    d = _decide("HEAD", flag=False)
    assert d.required is True
    assert d.evidence["mismatch"] == ["recomputed_true_flag_false"]


def test_evidence_is_json_serialisable_and_carries_no_free_text() -> None:
    d = _decide("CHEST", flag=True, headers=("HEAD", None))
    json.dumps(d.evidence, sort_keys=True)
    assert set(d.evidence) == {
        "study_body_part",
        "study_body_part_class",
        "study_body_part_source",
        "study_deface_required_flag",
        "series_header_body_parts",
        "mismatch",
    }


def test_decision_is_deterministic() -> None:
    args = {"body_part": "CHEST", "flag": True, "headers": ("HEAD", "CHEST")}
    assert _decide(**args) == _decide(**args)


def test_property_every_input_is_explained_and_not_required_needs_positive_evidence_everywhere():
    """Exhaustive grid. `required=False` must imply every single piece of evidence positively
    says known-non-head; and no input may ever produce an empty reason."""
    bodies = ["CHEST", "HEAD", "SINUSES", "XYZ", "SPINE", None, "", "  knee ", "C-SPINE"]
    sources = ["tag", "inferred", "unresolved", None]
    flags = [False, True, None]
    header_options = [
        (),
        (None,),
        ("CHEST",),
        ("HEAD",),
        ("XYZ",),
        ("CHEST", "HEAD"),
        ("KNEE", "ABDOMEN"),
        ("", "CHEST"),
    ]
    for body, source, flag, headers in itertools.product(bodies, sources, flags, header_options):
        d = _decide(body, source, flag, headers)
        assert d.reason.strip(), (body, source, flag, headers)
        assert d.method == ("none" if not d.required else "geometric_v1")
        if not d.required:
            assert classify_body_part(body) is BodyPartClass.KNOWN_NON_HEAD
            assert flag is False
            assert source != "unresolved"
            assert all(
                classify_body_part(h) is BodyPartClass.KNOWN_NON_HEAD
                for h in headers
                if h is not None and h.strip()
            )
        else:
            assert d.basis != "known_non_head"

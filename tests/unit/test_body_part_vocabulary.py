"""The closed body-part vocabulary (S7 plan §1). The property that matters: "defacing not
required" may only ever be concluded from a POSITIVE match against the known-non-head list.
Before this existed, any present value outside the head list (`SINUSES`, `ORBITS`, `XYZ`...)
was treated as resolved-and-not-a-head, so a real face CT with a slightly non-standard label
skipped defacing entirely.
"""

from __future__ import annotations

import random
import string

import pytest

from pipeline.catalog.models import Study
from pipeline.common.body_part import (
    AMBIGUOUS,
    HEAD_ADJACENT,
    KNOWN_NON_HEAD,
    BodyPartClass,
    classify_body_part,
    deface_required,
    infer_from_description,
    normalise,
)
from pipeline.stages.s2_parse_index.tags import resolve_deface_required
from pipeline.stages.s3_quality_gate.body_part import infer_body_part_from_text
from pipeline.stages.s3_quality_gate.checks import check_bodypart_unresolved

# Values the OLD code classified as (False, 'tag', v): resolved, not head, no defacing.
_FORMERLY_OPEN = [
    "SINUSES",
    "ORBITS",
    "FACIAL",
    "MAXILLOFACIAL",
    "JAW",
    "TEMPORAL",
    "CERVICAL",
    "HEAD_NECK",
]


@pytest.mark.parametrize("value", _FORMERLY_OPEN)
def test_formerly_open_head_synonyms_now_require_defacing(value: str) -> None:
    required, source, normalized = resolve_deface_required(value)
    assert required is True
    assert source == "tag"  # positively recognised as head-adjacent
    assert normalized == value.upper()


@pytest.mark.parametrize("value", ["XYZ", "FACE_3D", "['CHEST', 'ABDOMEN']", "SPINE", "WHOLEBODY"])
def test_unrecognised_or_ambiguous_values_are_fail_closed_and_unresolved(value: str) -> None:
    required, source, _ = resolve_deface_required(value)
    assert required is True
    assert source == "unresolved"  # so S3 tries to rescue it, then quarantines


def test_absent_is_unresolved_and_required() -> None:
    assert resolve_deface_required(None) == (True, "unresolved", None)
    assert resolve_deface_required("   ") == (True, "unresolved", None)


@pytest.mark.parametrize("value", ["CHEST", "  chest ", "ABDOMEN", "KNEE", "LSPINE", "L-SPINE"])
def test_only_a_positive_known_non_head_match_means_not_required(value: str) -> None:
    required, source, _ = resolve_deface_required(value)
    assert (required, source) == (False, "tag")


@pytest.mark.parametrize("value", ["HEAD", " head ", "C-SPINE", "PTS_HEAD", "HEAD_NECK"])
def test_head_adjacent_terms_including_separator_variants(value: str) -> None:
    assert classify_body_part(value) is BodyPartClass.HEAD_ADJACENT
    assert resolve_deface_required(value)[0] is True


def test_sets_are_disjoint_and_geometry_md_trigger_list_is_fully_covered() -> None:
    assert not (HEAD_ADJACENT & KNOWN_NON_HEAD)
    assert not (HEAD_ADJACENT & AMBIGUOUS)
    assert not (KNOWN_NON_HEAD & AMBIGUOUS)
    geometry_md = [
        "HEAD",
        "SKULL",
        "BRAIN",
        "FACE",
        "SINUS",
        "NECK",
        "TMJ",
        "ORBIT",
        "MAXILLA",
        "MANDIBLE",
        "CSPINE",
        "C-SPINE",
        "HEADNECK",
        "PTS_HEAD",
    ]
    assert all(classify_body_part(v) is BodyPartClass.HEAD_ADJACENT for v in geometry_md)


def test_invariant_not_required_implies_positive_known_non_head_match() -> None:
    """Fuzz: for ANY string, `required is False` must imply the normalised value is in the
    known-non-head list. Seeded, so deterministic."""
    rng = random.Random(7)
    alphabet = string.ascii_uppercase + string.digits + " _-/.^[]',"
    fuzz = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14))) for _ in range(4000)]
    adversarial = [
        *_FORMERLY_OPEN,
        r"CHEST\ABDOMEN",
        "CHESTHEAD",
        "HEADCHEST",
        "NOTCHEST",
        "CHEST ",
        "CHEST\t",
        "ｃｈｅｓｔ",
        "CHEST\x00",
        "\u200bCHEST",
        "CHEST\u200b",
    ]
    for value in fuzz + adversarial:
        required, _, _ = resolve_deface_required(value)
        if required is False:
            assert normalise(value) in KNOWN_NON_HEAD, repr(value)
        assert deface_required(classify_body_part(value)) == required


# ------------------------------------------------------------------------ inference


@pytest.mark.parametrize(
    "text, expected",
    [
        ("CT HEAD WO CONTRAST", "HEAD"),
        ("CT SINUSES", "SINUSES"),
        ("CT CERVICAL SPINE", "CERVICAL"),  # was: SPINE -> non-head. Now head-adjacent.
        ("CT C-SPINE", "CSPINE"),
        ("CT L SPINE", "LSPINE"),
        ("CT CHEST WITH CONTRAST", "CHEST"),
        ("CT CHEST ABDOMEN PELVIS", "CHEST"),
        ("CT CHEST ABDOMEN PELVIS AND NECK", "NECK"),  # any head-adjacent hit wins
    ],
)
def test_inference_matches_whole_tokens_and_prefers_head_adjacent(text: str, expected: str) -> None:
    assert infer_body_part_from_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "CT TREATMENT PLANNING",
        "ALARM SHIPPING PHARMACY HANDLING",
        "CT SPINE",
        "WHOLE BODY SCAN",
        "XYZ",
    ],
)
def test_inference_never_concludes_from_substrings_or_ambiguous_text(text: str | None) -> None:
    # `ARM` in ALARM, `HIP` in SHIPPING, `HAND` in HANDLING must not fire; bare SPINE is
    # ambiguous so it supports no "not a head" conclusion either.
    assert infer_body_part_from_text(text) is None
    assert infer_from_description(text) is None


# ------------------------------------------------------------------------ S3 check (pure)


def _study(body_part: str | None, source: str | None) -> Study:
    return Study(
        pseudo_study_uid="s",
        pseudo_patient_id="p",
        deface_required=True,
        body_part=body_part,
        body_part_source=source,
    )


@pytest.mark.parametrize("body_part", ["XYZ", "SPINE", "WHOLEBODY", "['CHEST', 'ABDOMEN']"])
def test_s3_quarantines_present_but_unrecognised_values_even_with_source_tag(
    body_part: str,
) -> None:
    """Rows written before the closed vocabulary existed carry source='tag' + an open value."""
    failure = check_bodypart_unresolved(_study(body_part, "tag"))
    assert failure is not None and failure.reason_code == "QG_BODYPART_UNRESOLVED"
    assert failure.detail["body_part_class"] in {"unrecognised", "ambiguous"}
    assert failure.detail["body_part"] == body_part


@pytest.mark.parametrize(
    "body_part, source",
    [("HEAD", "tag"), ("SINUSES", "tag"), ("CHEST", "tag"), ("HEAD", "inferred")],
)
def test_s3_passes_positively_recognised_values(body_part: str, source: str) -> None:
    assert check_bodypart_unresolved(_study(body_part, source)) is None


@pytest.mark.parametrize(
    "body_part, source", [(None, "unresolved"), ("CHEST", "unresolved"), (None, None)]
)
def test_s3_quarantines_absent_or_unresolved_source(
    body_part: str | None, source: str | None
) -> None:
    failure = check_bodypart_unresolved(_study(body_part, source))
    assert failure is not None

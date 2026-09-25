"""Pure logic tests for S4/S5's de-identification building blocks -- no live Postgres/
MinIO, no tesseract, no DB dependency at all. Per refs/TESTING.md Part 4: "tag profile,
UID determinism, date shift consistency."
"""

from __future__ import annotations

import json
from datetime import date

from pydicom.dataset import Dataset

from pipeline.common.deid_crypto import (
    patient_offset_days,
    pseudo_patient_id,
    pseudo_uid,
    shift_date,
)
from pipeline.common.deid_profile import (
    KEEP_TAGS,
    PRIVATE_TAG_WHITELIST,
    PRIVATE_TAG_WHITELIST_VERSION,
    TAG_PROFILE,
    TAG_PROFILE_BY_NAME,
    UID_REMAP_EXCLUDED_NAMES,
    UID_REMAP_TAG_NAMES,
)
from pipeline.stages.s4_deidentify.service import s4_params
from pipeline.stages.s4_deidentify.tagwalk import walk
from pipeline.stages.s5_verify.service import s5_params

SECRET_A = "test-secret-a"
SECRET_B = "test-secret-b"


# 1. pseudo_uid() / pseudo_patient_id() determinism


def test_pseudo_uid_is_deterministic_for_same_input_and_secret() -> None:
    assert pseudo_uid("1.2.3.4", SECRET_A) == pseudo_uid("1.2.3.4", SECRET_A)


def test_pseudo_uid_differs_for_different_secret() -> None:
    assert pseudo_uid("1.2.3.4", SECRET_A) != pseudo_uid("1.2.3.4", SECRET_B)


def test_pseudo_uid_differs_for_different_original() -> None:
    assert pseudo_uid("1.2.3.4", SECRET_A) != pseudo_uid("1.2.3.5", SECRET_A)


def test_pseudo_uid_format() -> None:
    value = pseudo_uid("1.2.840.10008.5.1.4.1.1.2", SECRET_A)
    assert value.startswith("2.25.")
    assert len(value) <= 64
    numeric_part = value.split(".", 2)[2]
    assert numeric_part.isdigit()
    assert numeric_part == "0" or not numeric_part.startswith("0")


def test_pseudo_patient_id_is_deterministic() -> None:
    assert pseudo_patient_id("PAT001", SECRET_A) == pseudo_patient_id("PAT001", SECRET_A)


def test_pseudo_patient_id_differs_for_different_secret() -> None:
    assert pseudo_patient_id("PAT001", SECRET_A) != pseudo_patient_id("PAT001", SECRET_B)


def test_pseudo_patient_id_never_equals_a_uid_shaped_value() -> None:
    # Distinct format from pseudo_uid() -- never collides with the provisional
    # "provisional.<uuid4>" format either (catalog/provisional.py).
    value = pseudo_patient_id("PAT001", SECRET_A)
    assert value.startswith("PP-")
    assert not value.startswith("2.25.")
    assert not value.startswith("provisional.")


# 2. patient_offset_days() / shift_date()


def test_patient_offset_days_is_deterministic() -> None:
    assert patient_offset_days("PAT001", SECRET_A) == patient_offset_days("PAT001", SECRET_A)


def test_patient_offset_days_in_expected_range() -> None:
    for patient_id in ("PAT001", "PAT002", "ANOTHER-ID", "X" * 40):
        offset = patient_offset_days(patient_id, SECRET_A)
        assert -3650 <= offset <= -1


def test_shift_date_basic_arithmetic() -> None:
    assert shift_date(date(2020, 1, 15), -10) == date(2020, 1, 5)


def test_shift_date_crosses_leap_year_boundary() -> None:
    # 2020 is a leap year -- Feb 29 exists. Shifting back across it must not skip/duplicate.
    assert shift_date(date(2020, 3, 1), -1) == date(2020, 2, 29)
    assert shift_date(date(2021, 3, 1), -1) == date(2021, 2, 28)  # 2021 is not a leap year


# 3. TAG_PROFILE completeness


def test_tag_profile_has_no_tag_also_in_keep_tags() -> None:
    profile_names = {rule.name for rule in TAG_PROFILE}
    assert profile_names.isdisjoint(KEEP_TAGS)


def test_tag_profile_covers_the_headline_phi_tags() -> None:
    # A representative sample from DICOM.md's table, not the full 43-row transcription --
    # a regression guard against the table silently losing entries.
    expected = {
        "PatientName": "Z",
        "PatientID": "Z",
        "PatientBirthDate": "Z",
        "InstitutionName": "X",
        "StudyDescription": "X",
        "StudyInstanceUID": "U",
        "SeriesInstanceUID": "U",
        "SOPInstanceUID": "U",
        "AccessionNumber": "Z",
    }
    for name, action in expected.items():
        assert name in TAG_PROFILE_BY_NAME, f"{name} missing from TAG_PROFILE"
        assert TAG_PROFILE_BY_NAME[name].action == action


def test_tag_profile_has_no_duplicate_tag_numbers() -> None:
    tags = [rule.tag for rule in TAG_PROFILE]
    assert len(tags) == len(set(tags))


# 4. walk() recursion


def test_walk_reaches_a_tag_nested_inside_a_sequence() -> None:
    inner = Dataset()
    inner.PatientName = "Nested^Patient"
    outer = Dataset()
    outer.PatientID = "X"
    outer.ReferencedStudySequence = [inner]

    visited_keywords: list[str] = []
    walk(outer, lambda parent, elem: visited_keywords.append(elem.keyword))

    assert "PatientName" in visited_keywords
    assert "PatientID" in visited_keywords


# 5. Private tag whitelist stays empty


def test_private_tag_whitelist_is_empty_by_design() -> None:
    assert len(PRIVATE_TAG_WHITELIST) == 0
    assert PRIVATE_TAG_WHITELIST_VERSION  # non-empty version string exists


# 6. UID remap allowlist excludes format/class UIDs


def test_uid_remap_allowlist_excludes_format_and_class_uids() -> None:
    for excluded in UID_REMAP_EXCLUDED_NAMES:
        assert excluded not in UID_REMAP_TAG_NAMES

    assert "SOPClassUID" in UID_REMAP_EXCLUDED_NAMES
    assert "TransferSyntaxUID" in UID_REMAP_EXCLUDED_NAMES
    assert "MediaStorageSOPClassUID" in UID_REMAP_EXCLUDED_NAMES


def test_uid_remap_allowlist_includes_the_identity_uids() -> None:
    for expected in (
        "SOPInstanceUID",
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "FrameOfReferenceUID",
    ):
        assert expected in UID_REMAP_TAG_NAMES


# 7. Security-sensitive regression: secrets never leak into transform_runs.parameters


def test_s4_params_never_contains_secret_values() -> None:
    params = s4_params()
    blob = json.dumps(params)
    assert SECRET_A not in blob
    assert SECRET_B not in blob
    # Only version identifiers -- no key even resembling a secret field name.
    assert "secret" not in json.dumps(list(params.keys())).lower()


def test_s5_params_never_contains_secret_values() -> None:
    params = s5_params()
    blob = json.dumps(params)
    assert SECRET_A not in blob
    assert SECRET_B not in blob
    assert "secret" not in json.dumps(list(params.keys())).lower()

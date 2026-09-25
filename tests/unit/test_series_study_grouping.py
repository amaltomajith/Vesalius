"""Tests the grouping *keys* (STAGES.md S2: instance -> series via SeriesInstanceUID,
series -> study via StudyInstanceUID) at the tag-extraction level, without a database.
Full grouping (find-or-create Study/Series rows) is exercised end-to-end in
tests/integration/test_s2_parse_index_pipeline.py against a live Postgres.
"""

from __future__ import annotations

import io

import pydicom

from pipeline.stages.s2_parse_index.tags import extract_tags
from tests.fixtures.dicom_factory import build_series


def _read(data: bytes) -> pydicom.dataset.Dataset:
    return pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)


def test_series_shares_study_and_series_uid_across_instances() -> None:
    series_bytes = build_series(count=3)
    tagged = [extract_tags(_read(b)) for b in series_bytes]

    study_uids = {t["StudyInstanceUID"] for t in tagged}
    series_uids = {t["SeriesInstanceUID"] for t in tagged}

    assert len(study_uids) == 1
    assert len(series_uids) == 1


def test_series_instances_have_distinct_sop_uids_and_instance_numbers() -> None:
    series_bytes = build_series(count=3)
    datasets = [_read(b) for b in series_bytes]

    sop_uids = {ds.SOPInstanceUID for ds in datasets}
    instance_numbers = sorted(int(ds.InstanceNumber) for ds in datasets)

    assert len(sop_uids) == 3
    assert instance_numbers == [1, 2, 3]


def test_series_instances_have_distinct_slice_positions() -> None:
    series_bytes = build_series(count=3)
    tagged = [extract_tags(_read(b)) for b in series_bytes]
    z_positions = [t["ImagePositionPatient"][2] for t in tagged]
    assert z_positions == sorted(z_positions)
    assert len(set(z_positions)) == 3


def test_two_independent_series_do_not_share_uids() -> None:
    series_a = [extract_tags(_read(b)) for b in build_series(count=2)]
    series_b = [extract_tags(_read(b)) for b in build_series(count=2)]

    study_a = {t["StudyInstanceUID"] for t in series_a}
    study_b = {t["StudyInstanceUID"] for t in series_b}
    assert study_a.isdisjoint(study_b)

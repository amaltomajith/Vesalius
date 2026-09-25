"""S3 quality-gate checks -- one test per active QG_* reason code, per refs/TESTING.md
Part 1 and the S3 plan. Requires a real Postgres (JSONB/ARRAY/UUID columns don't run on
SQLite) and MinIO (S3 re-reads bronze per instance), so gated behind
PIPELINE_INTEGRATION_TESTS even though it lives under tests/unit/ per the plan's file
layout -- same precedent as test_duplicate_detection.py.

QG_DUPLICATE_CONTENT has no test here: confirmed structurally unreachable at S3 (see
checks.py's module docstring) -- a check that cannot fire has nothing to trigger, per
TESTING.md rule 3's own logic.
"""

from __future__ import annotations

from pathlib import Path

from pydicom.uid import generate_uid
from sqlalchemy import select

from pipeline.catalog.models import Instance, Quarantine, Series, Study
from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_series
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series, build_series
from tests.fixtures.shared import integration


def _ingest_and_index(session, minio_client, settings, tmp_path: Path, series_bytes, name: str):
    archive = build_zip(
        tmp_path, name, {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(session, minio_client, settings, "pytest", str(archive))
    return parse_and_index_batch(session, minio_client, settings, batch.batch_id)


@integration
def test_ct_chest_clean_passes_no_deface_required(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, body_part="CHEST")
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "chest.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "pass"
    assert result.reason_codes == []
    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.deface_required is False


@integration
def test_ct_head_clean_passes_deface_required(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, body_part="HEAD")
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "head.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "pass"
    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.deface_required is True


@integration
def test_too_few_slices_quarantines(pipeline_session, bronze_minio_client, tmp_path: Path) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=2)
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "few.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_TOO_FEW_SLICES" in result.reason_codes


@integration
def test_spacing_nonuniform_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    # Clean series steps z by 2.0 each instance; instance 2 is shifted off that pattern.
    series_bytes = build_clean_ct_series(
        count=4, overrides={2: {"image_position": (0.0, 0.0, 4.3)}}
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "nonuniform.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_SPACING_NONUNIFORM" in result.reason_codes


@integration
def test_spacing_zero_quarantines(pipeline_session, bronze_minio_client, tmp_path: Path) -> None:
    settings = get_settings()
    # All three instances at the identical position -> every diff is 0, so the median is
    # unambiguously 0 (rather than merely close to another instance's position, which
    # with an odd count out can still produce a nonzero median of diffs).
    series_bytes = build_clean_ct_series(
        count=3,
        overrides={
            0: {"image_position": (0.0, 0.0, 0.0)},
            1: {"image_position": (0.0, 0.0, 0.0)},
            2: {"image_position": (0.0, 0.0, 0.0)},
        },
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "zero.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_SPACING_ZERO" in result.reason_codes


@integration
def test_slice_gap_quarantines(pipeline_session, bronze_minio_client, tmp_path: Path) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(
        count=4, overrides={3: {"image_position": (0.0, 0.0, 40.0)}}
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "gap.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_SLICE_GAP" in result.reason_codes


@integration
def test_orientation_mixed_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(
        count=3, overrides={2: {"orientation": [0, 1, 0, -1, 0, 0]}}
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "orient.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_ORIENTATION_MIXED" in result.reason_codes


@integration
def test_missing_geometry_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, overrides={1: {"omit_tags": ["PixelSpacing"]}})
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "missgeo.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_MISSING_GEOMETRY" in result.reason_codes


@integration
def test_missing_rescale_quarantines_ct(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(
        count=3, overrides={0: {"omit_tags": ["RescaleSlope", "RescaleIntercept"]}}
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "rescale.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_MISSING_RESCALE" in result.reason_codes


@integration
def test_pixel_decode_failed_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, overrides={1: {"pixel_data": "corrupt"}})
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "pixel.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_PIXEL_DECODE_FAILED" in result.reason_codes


@integration
def test_dimension_mismatch_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, overrides={2: {"rows": 8, "columns": 8}})
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "dim.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_DIMENSION_MISMATCH" in result.reason_codes


@integration
def test_bodypart_unresolved_quarantines_when_uninferrable(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, body_part=None)
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "unknown.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_BODYPART_UNRESOLVED" in result.reason_codes
    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.body_part_source == "unresolved"


@integration
def test_bodypart_inferred_from_study_description_resolves_and_passes(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(
        count=3, body_part=None, study_description="CT HEAD WO CONTRAST"
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "inferred.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert "QG_BODYPART_UNRESOLVED" not in result.reason_codes
    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.body_part_source == "inferred"
    assert study.body_part == "HEAD"
    assert study.deface_required is True


@integration
def test_modality_unsupported_quarantines(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=3, modality="US", pixel_data="valid")
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "us.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_MODALITY_UNSUPPORTED" in result.reason_codes


@integration
def test_bodypart_unresolved_propagates_to_every_series_in_study(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    shared_study_uid = generate_uid()
    series_a = build_clean_ct_series(count=3, body_part=None, study_uid=shared_study_uid)
    series_b = build_clean_ct_series(count=3, body_part=None, study_uid=shared_study_uid)
    archive = build_zip(
        tmp_path,
        "multi_series.zip",
        {
            **{f"a{i}.dcm": data for i, data in enumerate(series_a)},
            **{f"b{i}.dcm": data for i, data in enumerate(series_b)},
        },
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    indexed = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    series_ids = {r.series_id for r in indexed.indexed}
    assert len(series_ids) == 2

    results = [
        quality_gate_series(pipeline_session, bronze_minio_client, settings, sid)
        for sid in series_ids
    ]
    assert all(r.quality_status == "quarantined" for r in results)
    assert all("QG_BODYPART_UNRESOLVED" in r.reason_codes for r in results)


@integration
def test_multiple_simultaneous_failures_write_multiple_quarantine_rows(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(
        count=3,
        overrides={
            0: {"omit_tags": ["RescaleSlope", "RescaleIntercept"]},
            2: {"rows": 8, "columns": 8},
        },
    )
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "multi.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_MISSING_RESCALE" in result.reason_codes
    assert "QG_DIMENSION_MISMATCH" in result.reason_codes

    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S3",
                Quarantine.subject_type == "series",
                Quarantine.subject_id == series_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {r.reason_code for r in rows} == {"QG_MISSING_RESCALE", "QG_DIMENSION_MISMATCH"}


@integration
def test_quality_gate_idempotent_rerun_reconstructs_from_db(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=2)  # QG_TOO_FEW_SLICES
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "rerun.zip"
    )
    series_id = indexed.indexed[0].series_id

    first = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)
    second = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert second.quality_status == first.quality_status == "quarantined"
    assert set(second.reason_codes) == set(first.reason_codes) == {"QG_TOO_FEW_SLICES"}

    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S3",
                Quarantine.subject_type == "series",
                Quarantine.subject_id == series_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # not duplicated by the second, skipped run


@integration
def test_slice_position_and_series_spacing_persisted(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=4)
    indexed = _ingest_and_index(
        pipeline_session, bronze_minio_client, settings, tmp_path, series_bytes, "spacing.zip"
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)
    assert result.quality_status == "pass"

    series = pipeline_session.get(Series, series_id)
    assert series is not None
    assert series.slice_spacing is not None
    assert abs(series.slice_spacing - 2.0) < 1e-6
    assert series.spacing_uniform is True

    for r in indexed.indexed:
        instance = pipeline_session.get(Instance, r.instance_id)
        assert instance is not None
        assert instance.slice_position is not None


# --- closed body-part vocabulary (S7 plan §1) -----------------------------------------------


@integration
def test_head_synonym_body_part_passes_and_requires_defacing(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    """`SINUSES` used to be classed resolved-and-not-a-head (no defacing). It is a head term."""
    settings = get_settings()
    indexed = _ingest_and_index(
        pipeline_session,
        bronze_minio_client,
        settings,
        tmp_path,
        build_clean_ct_series(count=3, body_part="SINUSES"),
        "sinuses.zip",
    )
    result = quality_gate_series(
        pipeline_session, bronze_minio_client, settings, indexed.indexed[0].series_id
    )

    assert result.quality_status == "pass"
    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.deface_required is True
    assert study.body_part_source == "tag"


@integration
def test_unrecognised_body_part_quarantines_and_records_its_class(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    indexed = _ingest_and_index(
        pipeline_session,
        bronze_minio_client,
        settings,
        tmp_path,
        build_clean_ct_series(count=3, body_part="XYZ"),
        "xyz.zip",
    )
    series_id = indexed.indexed[0].series_id

    result = quality_gate_series(pipeline_session, bronze_minio_client, settings, series_id)

    assert result.quality_status == "quarantined"
    assert "QG_BODYPART_UNRESOLVED" in result.reason_codes
    (row,) = pipeline_session.execute(
        select(Quarantine).where(
            Quarantine.subject_id == series_id,
            Quarantine.stage == "S3",
            Quarantine.reason_code == "QG_BODYPART_UNRESOLVED",
        )
    ).scalars()
    assert row.detail["body_part"] == "XYZ"
    assert row.detail["body_part_class"] == "unrecognised"


@integration
def test_row_written_before_the_vocabulary_existed_is_still_caught(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    """Old rows carry source='tag' + an open value + deface_required=False. S3 must not trust
    the source alone."""
    settings = get_settings()
    indexed = _ingest_and_index(
        pipeline_session,
        bronze_minio_client,
        settings,
        tmp_path,
        build_clean_ct_series(count=3, body_part="CHEST"),
        "stale.zip",
    )
    study = pipeline_session.get(Study, indexed.indexed[0].study_id)
    assert study is not None
    study.body_part, study.body_part_source, study.deface_required = "XYZ", "tag", False
    pipeline_session.flush()

    result = quality_gate_series(
        pipeline_session, bronze_minio_client, settings, indexed.indexed[0].series_id
    )

    assert result.quality_status == "quarantined"
    assert "QG_BODYPART_UNRESOLVED" in result.reason_codes


@integration
def test_cervical_spine_description_infers_head_adjacent_not_bare_spine(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    """Previously "CT CERVICAL SPINE" matched the bare SPINE keyword and was stored as
    non-head. The cervical spine images the lower face/jaw: it must require defacing."""
    settings = get_settings()
    indexed = _ingest_and_index(
        pipeline_session,
        bronze_minio_client,
        settings,
        tmp_path,
        build_clean_ct_series(count=3, body_part=None, study_description="CT CERVICAL SPINE"),
        "cspine.zip",
    )
    result = quality_gate_series(
        pipeline_session, bronze_minio_client, settings, indexed.indexed[0].series_id
    )

    study = pipeline_session.get(Study, result.study_id)
    assert study is not None
    assert study.body_part == "CERVICAL"
    assert study.body_part_source == "inferred"
    assert study.deface_required is True

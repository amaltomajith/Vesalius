"""S6 through the REAL pipeline: ingest -> parse/index -> quality gate -> de-identify/verify
-> assemble volume, against live Postgres + MinIO. Only the tesseract engine is stubbed
(`no_ocr_engine`), so this runs on a bare host; the OCR channel has its own @ocr tests.

Assertions are scoped to the series/run ids each test creates -- never global counts -- since
the dev database can hold committed rows from manual/CLI runs.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pydicom
import pytest
from sqlalchemy import func, select

from pipeline.catalog.models import Artifact, Quarantine, Series, TransformRun
from pipeline.common.config import get_settings
from pipeline.orchestration.deid_pipeline import (
    deidentify_and_verify_batch,
    deidentify_and_verify_instance,
)
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import (
    assemble_volume_batch,
    assemble_volume_series,
)
from pipeline.storage.object_store import get_bytes
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import (
    build_asymmetric_marker_series,
    build_clean_ct_series,
    build_series,
)
from tests.fixtures.shared import integration
from tests.fixtures.volume_asserts import read_volume


def _ingest_to_s3(session, bronze, settings, blobs, tmp_path: Path, name: str = "s.zip"):  # type: ignore[no-untyped-def]
    archive = build_zip(tmp_path, name, {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(session, bronze, settings, "pytest", str(archive))
    parsed = parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)
    return batch, parsed


def _through_s5(session, bronze, silver, settings, blobs, tmp_path: Path, name: str = "s.zip"):  # type: ignore[no-untyped-def]
    batch, _ = _ingest_to_s3(session, bronze, settings, blobs, tmp_path, name)
    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    assert all(r.stage_failed is None for s in deid.series_results for r in s.results)
    return batch, deid.series_results[0].series_id


def _artifacts(session, series_id: UUID, kind: str) -> list[Artifact]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.kind == kind)
        ).scalars()
    )


@integration
def test_full_pipeline_writes_volume_and_meta_with_matching_lineage(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    blobs = build_clean_ct_series(count=4, body_part="CHEST")
    _, series_id = _through_s5(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, blobs, tmp_path
    )

    result = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert not result.skipped and not result.quarantined
    assert result.volume_object_key == f"{series_id}/volume.nii.gz"
    assert result.meta_object_key == f"{series_id}/meta.json"

    (volume_art,) = _artifacts(pipeline_session, series_id, "volume_nifti")
    (meta_art,) = _artifacts(pipeline_session, series_id, "volume_meta")
    assert volume_art.produced_by_run == meta_art.produced_by_run
    assert volume_art.zone == meta_art.zone == "silver"
    assert volume_art.contract_version == "1.0"

    run = pipeline_session.get(TransformRun, volume_art.produced_by_run)
    assert run is not None and run.stage == "S6" and run.status == "success"

    meta = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, result.meta_object_key)
    )
    assert meta["produced_by_run"] == str(run.id)
    assert meta["contract_version"] == "1.0"
    assert meta["spatial"]["coordinate_frame"] == "RAS"
    assert meta["array"]["axis_order"] == ["z", "y", "x"]
    assert meta["quality"]["instance_count"] == 4
    assert meta["intensity"]["units"] == "hounsfield_units"
    assert meta["intensity"]["hu_semantics_valid"] is True


@integration
def test_marker_survives_the_real_pipeline_and_the_stored_header_matches_meta(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    """The graded mirror/axis-swap deliverable, through S1..S6 and MinIO, on the object as
    stored in the silver bucket. Checks the voxel array AND the header a RAS consumer reads.
    """
    settings = get_settings()
    _, series_id = _through_s5(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        build_asymmetric_marker_series(),
        tmp_path,
    )

    result = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)
    stored = get_bytes(silver_minio_client, settings.minio_silver_bucket, result.volume_object_key)
    vol = read_volume(stored)

    assert vol.array.shape == (5, 8, 7)
    expected = np.zeros((5, 8, 7), dtype=np.float32)
    expected[4, 1, 5] = 3000.0
    assert np.allclose(vol.array, expected, atol=1e-3)
    for wrong in [(0, 1, 5), (4, 6, 5), (4, 1, 1), (4, 5, 1)]:
        # wrong indices for: sort-by-InstanceNumber, row mirror, column mirror, axis swap
        assert vol.array[wrong] != pytest.approx(3000.0)

    assert np.allclose(vol.sform @ np.array([5, 1, 4, 1.0]), [-5.0, -1.0, 4.0, 1.0], atol=1e-4)

    meta = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, result.meta_object_key)
    )
    assert np.allclose(np.array(meta["spatial"]["affine"]), vol.sform, atol=1e-5)
    # meta records slice order by pseudo SOP UID, in physical-z order (NOT InstanceNumber order)
    ordered = meta["quality"]["slice_order_pseudo_sop_uids"]
    silver_z = {}
    for art in _artifacts(pipeline_session, series_id, "deid_instance_dicom"):
        ds = pydicom.dcmread(
            io.BytesIO(get_bytes(silver_minio_client, settings.minio_silver_bucket, art.object_key))
        )
        silver_z[str(ds.SOPInstanceUID)] = float(ds.ImagePositionPatient[2])
    assert [silver_z[u] for u in ordered] == [0.0, 1.0, 2.0, 3.0, 4.0]


@integration
def test_series_with_an_unverified_instance_is_skipped_never_partially_assembled(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    _, parsed = _ingest_to_s3(
        pipeline_session, bronze_minio_client, settings, build_clean_ct_series(count=4), tmp_path
    )
    series_id = parsed.indexed[0].series_id
    for r in parsed.indexed[:3]:  # de-identify + verify only 3 of the 4 instances
        deidentify_and_verify_instance(
            pipeline_session, bronze_minio_client, silver_minio_client, settings, r.instance_id
        )

    result = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert result.skipped and result.skip_reason == "series_not_fully_verified_by_s5"
    assert _artifacts(pipeline_session, series_id, "volume_nifti") == []
    assert _artifacts(pipeline_session, series_id, "volume_meta") == []


@integration
def test_geometry_drift_against_the_s3_cache_quarantines_the_series(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    _, series_id = _through_s5(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        build_clean_ct_series(count=4),
        tmp_path,
    )
    series = pipeline_session.get(Series, series_id)
    series.slice_spacing = 999.0  # corrupt S3's persisted cache after the fact
    pipeline_session.flush()

    result = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert result.quarantined and result.reason_code == "S6_GEOMETRY_DRIFT"
    assert _artifacts(pipeline_session, series_id, "volume_nifti") == []
    (row,) = pipeline_session.execute(
        select(Quarantine).where(Quarantine.subject_id == series_id, Quarantine.stage == "S6")
    ).scalars()
    assert row.reason_code == "S6_GEOMETRY_DRIFT"
    assert row.detail["s3_cached_slice_spacing"] == 999.0  # the failing values, not just a code
    assert row.detail["s6_fresh_slice_spacing"] == pytest.approx(2.0)
    run = pipeline_session.execute(
        select(TransformRun).where(TransformRun.subject_id == series_id, TransformRun.stage == "S6")
    ).scalar_one()
    assert run.status == "failed" and run.reason == "S6_GEOMETRY_DRIFT"


@integration
def test_mr_series_assembles_without_claiming_hounsfield_semantics(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    blobs = build_series(
        count=3, modality="MR", body_part="CHEST", pixel_data="valid", rows=6, columns=5
    )
    _, series_id = _through_s5(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, blobs, tmp_path
    )

    result = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert not result.skipped and not result.quarantined
    meta = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, result.meta_object_key)
    )
    assert meta["modality"] == "MR"
    assert meta["intensity"]["units"] == "arbitrary"
    assert meta["intensity"]["hu_semantics_valid"] is False
    assert meta["nullability"]["padding_value"] == 0.0


@integration
def test_reassembling_is_idempotent_and_a_new_target_spacing_forces_a_rebuild(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    _, series_id = _through_s5(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        build_clean_ct_series(count=3),
        tmp_path,
    )

    first = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)
    second = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert first.volume_object_key == second.volume_object_key
    assert second.meta_object_key == first.meta_object_key
    assert len(_artifacts(pipeline_session, series_id, "volume_nifti")) == 1
    assert len(_artifacts(pipeline_session, series_id, "volume_meta")) == 1
    s6_success = pipeline_session.execute(
        select(func.count())
        .select_from(TransformRun)
        .where(
            TransformRun.subject_id == series_id,
            TransformRun.stage == "S6",
            TransformRun.status == "success",
        )
    ).scalar_one()
    assert s6_success == 1


@integration
def test_batch_entrypoint_assembles_every_verified_series(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    batch, series_id = _through_s5(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        build_clean_ct_series(count=3),
        tmp_path,
    )

    result = assemble_volume_batch(pipeline_session, silver_minio_client, settings, batch.batch_id)

    assert result.assembled_count == 1
    assert [r.series_id for r in result.series_results] == [series_id]
    assert result.series_results[0].volume_object_key == f"{series_id}/volume.nii.gz"


# --- skip bookkeeping: every skipped series must get its own transform_run, re-runnable ----


@integration
def test_two_different_unverified_series_can_both_be_skipped(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    ids = []
    for n in range(2):  # distinct series (fresh UIDs each build), neither ever run through S4/S5
        _, parsed = _ingest_to_s3(
            pipeline_session,
            bronze_minio_client,
            settings,
            build_clean_ct_series(count=3),
            tmp_path,
            name=f"s{n}.zip",
        )
        ids.append(parsed.indexed[0].series_id)
    assert ids[0] != ids[1]

    results = [
        assemble_volume_series(pipeline_session, silver_minio_client, settings, sid) for sid in ids
    ]

    assert all(r.skipped and r.skip_reason == "series_not_fully_verified_by_s5" for r in results)
    for sid in ids:
        skips = (
            pipeline_session.execute(
                select(TransformRun).where(
                    TransformRun.subject_id == sid,
                    TransformRun.stage == "S6",
                    TransformRun.status == "skipped",
                )
            )
            .scalars()
            .all()
        )
        assert len(skips) == 1


@integration
def test_re_running_s6_on_a_still_unverified_series_does_not_crash(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    _, parsed = _ingest_to_s3(
        pipeline_session, bronze_minio_client, settings, build_clean_ct_series(count=3), tmp_path
    )
    series_id = parsed.indexed[0].series_id

    first = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)
    again = assemble_volume_series(pipeline_session, silver_minio_client, settings, series_id)

    assert first.skipped and again.skipped
    n = pipeline_session.execute(
        select(func.count())
        .select_from(TransformRun)
        .where(TransformRun.subject_id == series_id, TransformRun.stage == "S6")
    ).scalar_one()
    assert n == 1  # the repeat reuses the recorded skip rather than inserting a duplicate

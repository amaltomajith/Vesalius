"""S8 against live Postgres + MinIO, on top of a REAL S7 run (S8's gate refusal / staleness
behaviour is entirely S7's gate logic, already exhaustively tested in test_defacing_gate.py and
test_s7_deface_pipeline.py; here the point is that S8 calls it correctly).

Two sources of series, mirroring test_s7_deface_pipeline.py: (1) a synthetic head phantom with
S6 seeded directly and S7 run for real, and (2) the real S1->S7 path for a chest CT.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Quarantine, Series, Study, TransformRun
from pipeline.common.config import get_settings
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_series
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.service import segment_batch, segment_series
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.deface_runs import make_s6_stub, phantom, phantom_nifti
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration

BASE = "lps_native_default"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _add_s6(session, silver, settings, series_id: UUID, volume_bytes, meta_bytes):  # type: ignore[no-untyped-def]
    run_id = uuid4()
    vkey, mkey = f"{series_id}/volume.nii.gz", f"{series_id}/meta.json"
    put_bytes(silver, settings.minio_silver_bucket, vkey, volume_bytes, "application/gzip")
    put_bytes(silver, settings.minio_silver_bucket, mkey, meta_bytes, "application/json")
    session.add(
        TransformRun(
            id=run_id,
            stage="S6",
            stage_version="1.0.0",
            idempotency_key=uuid4().hex,
            subject_type="series",
            subject_id=series_id,
            parameters={},
            input_hashes=[],
            output_hashes=[],
            status="success",
            started_at=datetime.now(UTC),
        )
    )
    session.flush()
    for kind, fmt, key, data in (
        ("volume_nifti", "nifti-1-gzip", vkey, volume_bytes),
        ("volume_meta", "json", mkey, meta_bytes),
    ):
        session.add(
            Artifact(
                id=uuid4(),
                series_id=series_id,
                kind=kind,
                format=fmt,
                zone="silver",
                object_key=key,
                sha256=_sha(data),
                bytes=len(data),
                produced_by_run=run_id,
            )
        )
    session.flush()


def _seed_head(session, silver, settings, *, body_part="HEAD", flag=True) -> UUID:  # type: ignore[no-untyped-def]
    study = Study(
        id=uuid4(),
        pseudo_study_uid=f"2.25.{uuid4().int}",
        pseudo_patient_id="P",
        modality="CT",
        body_part=body_part,
        body_part_source="tag",
        deface_required=flag,
    )
    session.add(study)
    session.flush()
    series = Series(
        id=uuid4(),
        study_id=study.id,
        pseudo_series_uid=f"2.25.{uuid4().int}",
        modality="CT",
        instance_count=0,
        quality_status="pass",
    )
    session.add(series)
    session.flush()
    ph = phantom(BASE)
    volume_bytes = phantom_nifti(BASE)
    stub = make_s6_stub(volume_bytes, ph.volume.shape, ph.grid.affine_ras, series_id=series.id)
    _add_s6(session, silver, settings, series.id, volume_bytes, stub.meta_bytes)
    return series.id


def _artifacts(session, series_id: UUID, kind: str) -> list[Artifact]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.kind == kind)
        ).scalars()
    )


def _s8_runs(session, series_id: UUID) -> list[TransformRun]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S8", TransformRun.subject_id == series_id
            )
        ).scalars()
    )


@integration
def test_a_head_is_segmented_after_a_real_s7_run_and_the_report_carries_the_deface_block(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_result = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert deface_result.applied is True

    result = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert not result.skipped and not result.quarantined
    assert result.mask_object_key is not None
    assert result.final_voxels and result.final_voxels > 0

    (run,) = _s8_runs(pipeline_session, sid)
    assert run.status == "success"
    assert run.parameters["deface"]["required"] is True
    assert run.parameters["deface"]["applied"] is True
    assert run.parameters["algorithm"]["preset"] == "bone"
    assert run.parameters["series_id"] == str(sid)

    (defaced_art,) = _artifacts(pipeline_session, sid, "volume_defaced")
    (s6_meta_art,) = _artifacts(pipeline_session, sid, "volume_meta")
    assert run.input_hashes == sorted([defaced_art.sha256, s6_meta_art.sha256])

    (mask_art,) = _artifacts(pipeline_session, sid, "mask")
    (report_art,) = _artifacts(pipeline_session, sid, "segmentation_report")
    assert mask_art.produced_by_run == report_art.produced_by_run == run.id
    assert mask_art.zone == "silver" and mask_art.contract_version == "1.0"
    assert run.output_hashes == [mask_art.sha256, report_art.sha256]

    stored = get_bytes(silver_minio_client, settings.minio_silver_bucket, mask_art.object_key)
    assert _sha(stored) == mask_art.sha256
    body = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, report_art.object_key)
    )
    assert body["deface"]["reason"] == "body_part=HEAD"
    assert body["stats"]["final_voxels"] == result.final_voxels


@integration
def test_bone_and_skin_surface_presets_coexist_as_independent_runs(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)

    bone = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    skin = segment_series(pipeline_session, silver_minio_client, settings, sid, "skin_surface")

    assert bone.mask_object_key != skin.mask_object_key
    assert len(_s8_runs(pipeline_session, sid)) == 2
    assert len(_artifacts(pipeline_session, sid, "mask")) == 2


@integration
def test_a_rerun_of_the_same_preset_is_idempotent_and_returns_an_equal_result(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)

    first = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    runs = {r.id for r in _s8_runs(pipeline_session, sid)}
    arts = {a.id for a in _artifacts(pipeline_session, sid, "mask")}

    second = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert second == first
    assert {r.id for r in _s8_runs(pipeline_session, sid)} == runs
    assert {a.id for a in _artifacts(pipeline_session, sid, "mask")} == arts


@integration
def test_a_rerun_over_a_succeeded_run_with_a_missing_mask_artifact_crashes_loudly(
    pipeline_session, silver_minio_client
) -> None:
    """Guards the idempotency invariant itself: a successful transform_run must always have its
    artifacts. If the catalog is ever corrupted so that it doesn't, a rerun over the SAME inputs
    (which takes the "already succeeded, skip the work" path) must fail loudly rather than
    silently return a result with no mask -- CLAUDE.md's "fail loudly on data correctness"."""
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    (mask_art,) = _artifacts(pipeline_session, sid, "mask")
    pipeline_session.delete(mask_art)
    pipeline_session.flush()

    with pytest.raises(AssertionError, match="idempotency invariant violated"):
        segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")


@integration
def test_a_head_with_no_s7_run_is_skipped_by_the_gate_refusal_not_quarantined(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)

    result = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped and not result.quarantined
    assert result.gate_reason_code == "GATE_NO_S7_RUN"
    assert result.skip_reason == "defacing_gate_refused:GATE_NO_S7_RUN"
    (run,) = _s8_runs(pipeline_session, sid)
    assert run.status == "skipped"
    assert run.parameters["gate_code"] == "GATE_NO_S7_RUN"
    assert run.parameters["series_id"] == str(sid)
    assert _artifacts(pipeline_session, sid, "mask") == []
    q = (
        pipeline_session.execute(select(Quarantine).where(Quarantine.subject_id == sid))
        .scalars()
        .all()
    )
    assert q == []


@integration
def test_a_tampered_defaced_object_makes_s8_skip_via_the_gates_own_hash_check(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    (defaced,) = _artifacts(pipeline_session, sid, "volume_defaced")
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        defaced.object_key,
        b"corrupted",
        "application/gzip",
    )

    result = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped
    assert result.gate_reason_code == "GATE_DEFACED_HASH_MISMATCH"
    assert _artifacts(pipeline_session, sid, "mask") == []


@integration
def test_two_series_lacking_an_s7_run_in_one_batch_get_distinct_skip_rows(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sids = [_seed_head(pipeline_session, silver_minio_client, settings) for _ in range(2)]
    results = [
        segment_series(pipeline_session, silver_minio_client, settings, s, "bone") for s in sids
    ]
    assert all(r.skipped for r in results)
    for s in sids:
        assert len(_s8_runs(pipeline_session, s)) == 1
    # and re-running over an already-skipped series is a no-op, not a UNIQUE violation
    again = segment_series(pipeline_session, silver_minio_client, settings, sids[0], "bone")
    assert again.skipped
    assert len(_s8_runs(pipeline_session, sids[0])) == 1


@integration
def test_an_unsupported_preset_raises_before_touching_the_gate_or_the_database(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    with pytest.raises(ValueError, match="unsupported preset"):
        segment_series(pipeline_session, silver_minio_client, settings, sid, "vessel_ce")
    assert _s8_runs(pipeline_session, sid) == []


@integration
def test_an_unknown_series_id_is_a_value_error(pipeline_session, silver_minio_client) -> None:
    with pytest.raises(ValueError):
        segment_series(pipeline_session, silver_minio_client, get_settings(), uuid4(), "bone")


# ---- the real S1 -> S7 path for a chest CT ----------------------------------------------------


def _chest_through_s7(  # type: ignore[no-untyped-def]
    session,
    bronze,
    silver,
    settings,
    tmp_path: Path,
    name: str,
    *,
    count: int = 4,
    extra: dict | None = None,
) -> tuple[UUID, UUID]:
    blobs = build_clean_ct_series(count=count, body_part="CHEST", **(extra or {}))
    archive = build_zip(tmp_path, name, {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(session, bronze, settings, "pytest", str(archive))
    parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)
    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    series_id = deid.series_results[0].series_id
    assemble_volume_series(session, silver, settings, series_id)
    deface_series(session, silver, settings, series_id)
    return series_id, batch.batch_id


@integration
def test_a_real_chest_series_segments_the_s6_volume_and_the_bone_mask_is_empty(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    """The factory's default pixels rescale to roughly [-1024, -925] HU (air), well below
    `bone`'s [300, 3000] range, so `bone` legitimately empties on this real pipeline output --
    proving `S8_EMPTY_MASK` fires end to end, not just on hand-built arrays."""
    settings = get_settings()
    sid, _ = _chest_through_s7(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "a.zip"
    )

    result = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.quarantined
    assert result.reason_code == "S8_EMPTY_MASK"


@integration
def test_a_real_chest_series_soft_tissue_preset_segments_successfully(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    # the factory's DEFAULT pixels rescale to roughly [-1024, -925] HU -- below every preset's
    # range, including soft_tissue's -- so this series gets a uniform raw value that rescales to
    # 0 HU (water, squarely inside soft_tissue's [-50, 300]) AND enough in-plane/slice extent
    # (the factory's 4x4-pixel default resamples to a 2x2x8 volume, too thin for closing(2) then
    # opening(1) to leave anything at all -- a real morphological effect, not a bug; see
    # segmentation.py) to survive cleanup and actually have something to segment
    sid, batch_id = _chest_through_s7(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        tmp_path,
        "b.zip",
        count=10,
        extra={"uniform_raw": 1024, "rows": 20, "columns": 20, "pixel_spacing": (1.0, 1.0)},
    )

    result = segment_series(pipeline_session, silver_minio_client, settings, sid, "soft_tissue")

    assert not result.skipped and not result.quarantined
    assert result.mask_object_key is not None

    out = segment_batch(
        pipeline_session, silver_minio_client, settings, batch_id, presets=("soft_tissue",)
    )
    assert [r.series_id for r in out.series_results] == [sid]
    assert out.segmented_count == 1

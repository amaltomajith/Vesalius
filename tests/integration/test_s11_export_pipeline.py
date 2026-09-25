"""S11 against live Postgres + MinIO, on top of REAL S7, S8, S9 and S10 runs. The point of this
tier: S11 finds S10's mesh correctly (the provenance walk in `latest_s10_mesh`), the gate
supplies the gold volume copy, and -- the single most important behavioural test in this stage --
a SECOND real export (after S10's mesh changes) overwrites the SAME gold keys and updates the
SAME four `Artifact` rows in place, proving the upsert design directly, live.

Two sources of series, mirroring test_s7-s10_..._pipeline.py: (1) a synthetic head phantom with
S6 seeded directly and S7+S8+S9+S10 run for real, and (2) the real S1->S10 path for a chest CT.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Series, Study, TransformRun
from pipeline.common.config import get_settings
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_series
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.service import segment_series
from pipeline.stages.s9_surface_extraction.service import extract_surface
from pipeline.stages.s10_mesh_postprocess.service import postprocess_mesh_series
from pipeline.stages.s11_export.glb_io import load_glb
from pipeline.stages.s11_export.service import export_batch, export_series
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


def _gold_artifacts(session, series_id: UUID) -> dict[str, Artifact]:  # type: ignore[no-untyped-def]
    return {
        a.kind: a
        for a in session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.zone == "gold")
        ).scalars()
    }


def _s11_runs(session, series_id: UUID) -> list[TransformRun]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S11", TransformRun.subject_id == series_id
            )
        ).scalars()
    )


def _head_through_s10(session, silver, settings, preset="bone", target_faces=50_000):  # type: ignore[no-untyped-def]
    sid = _seed_head(session, silver, settings)
    deface_series(session, silver, settings, sid)
    segment_series(session, silver, settings, sid, preset)
    extract_surface(session, silver, settings, sid, preset)
    postprocess_mesh_series(session, silver, settings, sid, preset, target_faces=target_faces)
    return sid


@integration
def test_a_head_exports_successfully_after_real_s7_s8_s9_s10_runs(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)

    result = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert not result.skipped and not result.quarantined
    assert result.glb_object_key == f"{sid}/surface.glb"
    assert result.volume_object_key == f"{sid}/volume.nii.gz"
    assert result.thumbnail_object_key == f"{sid}/thumbnail.png"
    assert result.manifest_object_key == f"{sid}/manifest.json"
    assert result.face_count and result.face_count > 0

    (run,) = _s11_runs(pipeline_session, sid)
    assert run.status == "success"
    assert run.parameters["deface"]["required"] is True
    assert run.parameters["source_parameters"]["segment"]["preset"] == "bone"
    assert run.parameters["source_parameters"]["mesh_postprocess"]["target_faces"] == 50_000

    (mesh_art,) = list(
        pipeline_session.execute(
            select(Artifact).where(
                Artifact.series_id == sid, Artifact.kind == "surface_mesh_optimized"
            )
        ).scalars()
    )
    # the head phantom requires defacing, so the gate serves S7's `volume_defaced`, not S6's
    # raw `volume_nifti` -- see gate.py's SOURCE_DEFACED branch
    (volume_art,) = list(
        pipeline_session.execute(
            select(Artifact).where(Artifact.series_id == sid, Artifact.kind == "volume_defaced")
        ).scalars()
    )
    (meta_art,) = list(
        pipeline_session.execute(
            select(Artifact).where(Artifact.series_id == sid, Artifact.kind == "volume_meta")
        ).scalars()
    )
    assert run.input_hashes == sorted([mesh_art.sha256, volume_art.sha256, meta_art.sha256])

    arts = _gold_artifacts(pipeline_session, sid)
    assert set(arts) == {"surface_glb", "volume_nifti", "thumbnail", "manifest"}
    for a in arts.values():
        assert a.produced_by_run == run.id
        assert a.zone == "gold"
        stored = get_bytes(gold_minio_client, settings.minio_gold_bucket, a.object_key)
        assert _sha(stored) == a.sha256

    manifest = json.loads(
        get_bytes(gold_minio_client, settings.minio_gold_bucket, arts["manifest"].object_key)
    )
    assert manifest["geometry"]["source_frame"] == "RAS_mm"
    assert manifest["deface"]["reason"] == "body_part=HEAD"

    glb_bytes = get_bytes(
        gold_minio_client, settings.minio_gold_bucket, arts["surface_glb"].object_key
    )
    verts, faces, normals, watertight = load_glb(glb_bytes)
    assert len(faces) == result.face_count


@integration
def test_a_rerun_of_the_same_preset_is_idempotent_and_returns_an_equal_result(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)

    first = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    runs = {r.id for r in _s11_runs(pipeline_session, sid)}
    art_ids = {a.id for a in _gold_artifacts(pipeline_session, sid).values()}

    second = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert second == first
    assert {r.id for r in _s11_runs(pipeline_session, sid)} == runs
    assert {a.id for a in _gold_artifacts(pipeline_session, sid).values()} == art_ids


@integration
def test_a_second_export_after_a_mesh_change_overwrites_the_same_gold_keys_in_place(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    """The single most important behavioural test in this stage (see the plan's own §7/§4): a
    naive S7-S10-style "always insert" here would leave stale Artifact.sha256 the moment gold
    gets rebuilt -- this proves the upsert directly, against a real second S10 run."""
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings, target_faces=50_000)
    first = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    first_arts = _gold_artifacts(pipeline_session, sid)
    # captured as plain values, not held as ORM object references -- SQLAlchemy's identity map
    # means `first_arts["surface_glb"]` is the SAME Python object the second export mutates in
    # place, so re-reading `.sha256` off it after that update would silently show the NEW value
    first_ids = {kind: a.id for kind, a in first_arts.items()}
    first_keys = {kind: a.object_key for kind, a in first_arts.items()}
    first_glb_sha = first_arts["surface_glb"].sha256

    # a real second S10 run at a different target_faces -> a genuinely different optimised mesh
    postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "bone", target_faces=10_000
    )
    second = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert not second.skipped
    assert second.face_count != first.face_count

    (run1, run2) = sorted(_s11_runs(pipeline_session, sid), key=lambda r: r.started_at)
    assert run1.id != run2.id  # a genuinely new transform_run, not a skip

    second_arts = _gold_artifacts(pipeline_session, sid)
    assert set(second_arts) == set(first_ids)  # same four kinds, never a fifth row
    for kind, art in second_arts.items():
        assert art.id == first_ids[kind]  # SAME row, UPDATED -- never a second insert
        assert art.object_key == first_keys[kind]  # same fixed gold key
        assert art.produced_by_run == run2.id  # points at the run that most recently wrote it

    # and the object actually AT that key now matches the second run's bytes, not the first's
    stored_glb = get_bytes(
        gold_minio_client, settings.minio_gold_bucket, second_arts["surface_glb"].object_key
    )
    assert _sha(stored_glb) == second_arts["surface_glb"].sha256
    assert second_arts["surface_glb"].sha256 != first_glb_sha


@integration
def test_a_gate_refusal_on_a_non_defaced_series_is_skipped_not_quarantined(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    # no S7 run at all -- the gate must refuse

    result = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert result.skipped and not result.quarantined
    assert result.skip_reason.startswith("defacing_gate_refused:")
    assert _gold_artifacts(pipeline_session, sid) == {}


@integration
def test_a_head_with_no_s10_mesh_for_the_preset_is_skipped_not_quarantined(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")
    # no S10 run at all

    result = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert result.skipped and not result.quarantined
    assert result.skip_reason == "series_has_no_s10_mesh"
    (run,) = _s11_runs(pipeline_session, sid)
    assert run.status == "skipped"
    assert run.parameters["series_id"] == str(sid)


@integration
def test_a_second_preset_on_the_same_series_finds_its_own_mesh_not_the_others(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    """`latest_s10_mesh`'s provenance walk must actually distinguish presets on the SAME
    series, not just across series (test_a_head_exports... only ever builds one preset)."""
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")
    # different target_faces per preset so decimation can't coincidentally cap both to the
    # same final count and mask the distinction this test exists to prove
    postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "bone", target_faces=5_000
    )
    segment_series(pipeline_session, silver_minio_client, settings, sid, "skin_surface")
    extract_surface(pipeline_session, silver_minio_client, settings, sid, "skin_surface")
    postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "skin_surface", target_faces=8_000
    )

    # independently determine each preset's ACTUAL optimised face count, by tracing S10 ->
    # (via its lone input_hash) -> S9's own recorded preset -- the same provenance property
    # `latest_s10_mesh` relies on, derived here independently rather than trusted
    opt_arts = list(
        pipeline_session.execute(
            select(Artifact).where(
                Artifact.series_id == sid, Artifact.kind == "surface_mesh_optimized"
            )
        ).scalars()
    )
    assert len(opt_arts) == 2
    face_count_by_preset: dict[str, int] = {}
    for art in opt_arts:
        s10_run = pipeline_session.get(TransformRun, art.produced_by_run)
        (s9_art,) = list(
            pipeline_session.execute(
                select(Artifact).where(
                    Artifact.series_id == sid, Artifact.sha256 == s10_run.input_hashes[0]
                )
            ).scalars()
        )
        s9_run = pipeline_session.get(TransformRun, s9_art.produced_by_run)
        preset = s9_run.parameters["algorithm"]["preset"]
        from pipeline.stages.s10_mesh_postprocess.mesh_io import load_mesh as load_optimized

        mesh_bytes = get_bytes(silver_minio_client, settings.minio_silver_bucket, art.object_key)
        _, faces, _ = load_optimized(mesh_bytes)
        face_count_by_preset[preset] = len(faces)
    assert face_count_by_preset["bone"] != face_count_by_preset["skin_surface"]

    bone_result = export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid, "bone"
    )
    assert not bone_result.skipped
    assert bone_result.face_count == face_count_by_preset["bone"]

    (bone_run,) = _s11_runs(pipeline_session, sid)
    assert bone_run.parameters["source_parameters"]["surface_extraction"]["preset"] == "bone"
    assert bone_run.parameters["preset"] == "bone"


@integration
def test_a_tampered_optimised_mesh_object_is_refused(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    (mesh_art,) = list(
        pipeline_session.execute(
            select(Artifact).where(
                Artifact.series_id == sid, Artifact.kind == "surface_mesh_optimized"
            )
        ).scalars()
    )
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        mesh_art.object_key,
        b"corrupted",
        "application/octet-stream",
    )

    result = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert result.quarantined
    assert result.reason_code == "S11_INPUT_TAMPERED"


@integration
def test_an_unknown_series_id_is_a_value_error(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    with pytest.raises(ValueError):
        export_series(
            pipeline_session, silver_minio_client, gold_minio_client, get_settings(), uuid4()
        )


# ---- the real S1 -> S10 path for a chest CT ----------------------------------------------------


def _chest_through_s10(session, bronze, silver, settings, tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
    blobs = build_clean_ct_series(
        count=10,
        body_part="CHEST",
        uniform_raw=1024,
        rows=20,
        columns=20,
        pixel_spacing=(1.0, 1.0),
    )
    archive = build_zip(tmp_path, name, {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(session, bronze, settings, "pytest", str(archive))
    parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)
    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    series_id = deid.series_results[0].series_id
    assemble_volume_series(session, silver, settings, series_id)
    deface_series(session, silver, settings, series_id)
    segment_series(session, silver, settings, series_id, "soft_tissue")
    extract_surface(session, silver, settings, series_id, "soft_tissue")
    postprocess_mesh_series(session, silver, settings, series_id, "soft_tissue")
    return series_id, batch.batch_id


@integration
def test_a_real_chest_series_exports_with_deface_not_required_recorded(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    gold_minio_client,
    no_ocr_engine,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    sid, batch_id = _chest_through_s10(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        tmp_path,
        "c.zip",
    )

    result = export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid, "soft_tissue"
    )

    assert not result.skipped and not result.quarantined
    manifest = json.loads(
        get_bytes(
            gold_minio_client,
            settings.minio_gold_bucket,
            _gold_artifacts(pipeline_session, sid)["manifest"].object_key,
        )
    )
    assert manifest["deface"]["required"] is False

    out = export_batch(
        pipeline_session,
        silver_minio_client,
        gold_minio_client,
        settings,
        batch_id,
        preset="soft_tissue",
    )
    assert [r.series_id for r in out.series_results] == [sid]
    assert out.exported_count == 1

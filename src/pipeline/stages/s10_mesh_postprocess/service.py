"""S10 -- Mesh Post-processing. See refs/STAGES.md S10, refs/GEOMETRY.md.

Reads S9's raw mesh ONLY via its `Artifact` row + object store, never by importing
`s9_surface_extraction.service`/`mesh_extract` (extends the existing static guard). No defacing
gate call here: the mesh is already downstream of S7's gate-verified provenance (S9 only ever
produces one from a gate-approved volume), so S10 operating on it reintroduces no risk. The
`deface` block is still carried forward from S9's own report -- read, never re-derived -- purely
for audit continuity, matching every other stage's report.

No S9 mesh yet for the requested `(series, preset)` is a `skipped` transform_run, never a
quarantine (S7-S9 precedent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    idempotency_key,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import (
    MeshInputTamperedError,
    MeshOutputMismatchError,
    MeshUnreadableInputError,
    QuarantineError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s8_segment.params import SUPPORTED_PRESETS
from pipeline.stages.s9_surface_extraction import mesh_io as raw_mesh_io
from pipeline.stages.s10_mesh_postprocess import mesh_io, params
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh
from pipeline.stages.s10_mesh_postprocess.models import (
    BatchMeshPostprocessResult,
    SeriesMeshPostprocessResult,
)
from pipeline.stages.s10_mesh_postprocess.report import build_report
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


@dataclass(frozen=True)
class _MeshRef:
    object_key: str
    sha256: str
    deface_block: dict[str, Any]


def latest_s9_mesh(session: Session, series_id: UUID, preset: str) -> _MeshRef | None:
    """The `surface_mesh_raw` artifact of the latest SUCCESSFUL S9 run for `(series_id,
    preset)`, plus that run's recorded `deface` block, carried forward for audit. `preset`
    lives inside `parameters.algorithm.preset` (S8/S9 precedent -- no dedicated column)."""
    runs = (
        session.execute(
            select(TransformRun)
            .where(
                TransformRun.stage == "S9",
                TransformRun.subject_type == "series",
                TransformRun.subject_id == series_id,
                TransformRun.status == "success",
            )
            .order_by(TransformRun.started_at.desc())
        )
        .scalars()
        .all()
    )
    for run in runs:
        algo = run.parameters.get("algorithm") if isinstance(run.parameters, dict) else None
        if isinstance(algo, dict) and algo.get("preset") == preset:
            art = session.execute(
                select(Artifact).where(
                    Artifact.produced_by_run == run.id, Artifact.kind == "surface_mesh_raw"
                )
            ).scalar_one_or_none()
            if art is None:
                return None
            deface = run.parameters.get("deface")
            return _MeshRef(
                object_key=art.object_key,
                sha256=art.sha256,
                deface_block=deface if isinstance(deface, dict) else {},
            )
    return None


def _read_verified(client: Minio, settings: Settings, ref: _MeshRef) -> bytes:
    try:
        data = get_bytes(client, settings.minio_silver_bucket, ref.object_key)
    except Exception as exc:  # noqa: BLE001 -- infra failure, S6-S9 precedent: quarantine
        raise MeshUnreadableInputError(
            {"object_key": ref.object_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc
    actual = sha256_of_bytes(data)
    if actual != ref.sha256:
        raise MeshInputTamperedError(
            {"object_key": ref.object_key, "catalog_sha256": ref.sha256, "actual_sha256": actual}
        )
    return data


@dataclass
class _Produced:
    outcome: StageOutcome
    mesh_bytes: bytes
    mesh_key: str
    report_bytes: bytes
    report_key: str
    stats: dict[str, Any]


def _execute(
    *,
    series_id: UUID,
    mesh_ref: _MeshRef,
    raw_mesh_bytes: bytes,
    preset: str,
    target_faces: int,
    prefix: str,
) -> _Produced:
    try:
        vertices, faces = raw_mesh_io.load_mesh(raw_mesh_bytes)
    except Exception as exc:  # noqa: BLE001 -- read/parse failure, not a post-process verdict
        raise MeshUnreadableInputError({"error": f"{type(exc).__name__}: {exc}"}) from exc

    result = postprocess_mesh(
        vertices,
        faces,
        taubin_lamb=params.TAUBIN_LAMB,
        taubin_nu=params.TAUBIN_NU,
        taubin_iterations=params.TAUBIN_ITERATIONS,
        target_faces=target_faces,
    )

    mesh_bytes = mesh_io.write_mesh(result.vertices, result.faces, result.normals)
    reread_verts, reread_faces, reread_normals = mesh_io.load_mesh(mesh_bytes)
    verts_match = reread_verts.shape == result.vertices.shape and np.allclose(
        reread_verts, result.vertices, atol=params.VERTEX_ROUND_TRIP_TOLERANCE_MM
    )
    normals_match = reread_normals.shape == result.normals.shape and np.allclose(
        reread_normals, result.normals, atol=params.NORMAL_ROUND_TRIP_TOLERANCE
    )
    if not (verts_match and normals_match and np.array_equal(reread_faces, result.faces)):
        raise MeshOutputMismatchError(
            {
                "vertex_count_written": int(len(result.vertices)),
                "vertex_count_reread": int(len(reread_verts)),
                "face_count_written": int(len(result.faces)),
                "face_count_reread": int(len(reread_faces)),
            }
        )

    mesh_key = f"{prefix}/surface_optimized_{preset}.ply"
    report_key = f"{prefix}/mesh_postprocess_report.json"
    output_ref = {"object_key": mesh_key, "sha256": sha256_of_bytes(mesh_bytes)}
    report = build_report(
        series_id=series_id,
        preset=preset,
        target_faces=target_faces,
        deface_block=mesh_ref.deface_block,
        input_ref={"object_key": mesh_ref.object_key, "sha256": mesh_ref.sha256},
        output_ref=output_ref,
        report_key=report_key,
        stats=result.stats,
    )
    return _Produced(
        StageOutcome(output_hashes=[output_ref["sha256"], sha256_of_bytes(report)]),
        mesh_bytes,
        mesh_key,
        report,
        report_key,
        result.stats,
    )


def postprocess_mesh_series(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    series_id: UUID,
    preset: str,
    *,
    target_faces: int = params.DEFAULT_TARGET_FACES,
) -> SeriesMeshPostprocessResult:
    if preset not in SUPPORTED_PRESETS:
        raise ValueError(f"unsupported preset: {preset!r} (supported: {SUPPORTED_PRESETS})")
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    mesh_ref = latest_s9_mesh(session, series_id, preset)
    if mesh_ref is None:
        reason = "series_has_no_s9_mesh"
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id), "preset": preset, "skip": reason},
            ),
            reason=reason,
        )
        return SeriesMeshPostprocessResult(
            series_id=series_id, preset=preset, skipped=True, skip_reason=reason
        )

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=[mesh_ref.sha256],
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": params.algorithm_params(target_faces),
            "deface": mesh_ref.deface_block,
        },
    )
    prefix = f"{series_id}/optimized/{idempotency_key(ctx)[:16]}"
    holder: list[_Produced] = []

    def _do() -> StageOutcome:
        raw_mesh_bytes = _read_verified(silver_client, settings, mesh_ref)
        produced = _execute(
            series_id=series_id,
            mesh_ref=mesh_ref,
            raw_mesh_bytes=raw_mesh_bytes,
            preset=preset,
            target_faces=target_faces,
            prefix=prefix,
        )
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            produced.mesh_key,
            produced.mesh_bytes,
            "application/octet-stream",
        )
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            produced.report_key,
            produced.report_bytes,
            "application/json",
        )
        holder.append(produced)
        return produced.outcome

    try:
        run, executed = run_stage(session, ctx, _do)
    except QuarantineError as exc:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="series",
                subject_id=series_id,
                stage=STAGE,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        return SeriesMeshPostprocessResult(
            series_id=series_id, preset=preset, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        arts = {
            a.kind: a
            for a in session.execute(select(Artifact).where(Artifact.produced_by_run == run.id))
            .scalars()
            .all()
        }
        if "surface_mesh_optimized" not in arts or "mesh_postprocess_report" not in arts:
            raise AssertionError(
                f"S10 transform_run {run.id} succeeded previously but artifact rows are missing "
                "-- idempotency invariant violated"
            )
        report_bytes = get_bytes(
            silver_client, settings.minio_silver_bucket, arts["mesh_postprocess_report"].object_key
        )
        stats = json.loads(report_bytes)["stats"]
        return SeriesMeshPostprocessResult(
            series_id=series_id,
            preset=preset,
            mesh_object_key=arts["surface_mesh_optimized"].object_key,
            report_object_key=arts["mesh_postprocess_report"].object_key,
            input_faces=stats["input_faces"],
            final_face_count=stats["final_face_count"],
        )

    produced = holder[0]
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="surface_mesh_optimized",
            format="ply",
            zone="silver",
            object_key=produced.mesh_key,
            sha256=sha256_of_bytes(produced.mesh_bytes),
            bytes=len(produced.mesh_bytes),
            contract_version="1.0",
            produced_by_run=run.id,
        )
    )
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="mesh_postprocess_report",
            format="json",
            zone="silver",
            object_key=produced.report_key,
            sha256=sha256_of_bytes(produced.report_bytes),
            bytes=len(produced.report_bytes),
            produced_by_run=run.id,
        )
    )
    session.flush()
    return SeriesMeshPostprocessResult(
        series_id=series_id,
        preset=preset,
        mesh_object_key=produced.mesh_key,
        report_object_key=produced.report_key,
        input_faces=produced.stats["input_faces"],
        final_face_count=produced.stats["final_face_count"],
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors the identical helper in S2-S9 (consolidation deferred rather than touching
    verified code)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    return [row[0][0] for row in session.execute(stmt).all() if row[0]]


def postprocess_mesh_batch(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    batch_id: UUID,
    presets: tuple[str, ...] = (params.DEFAULT_PRESET,),
    *,
    target_faces: int = params.DEFAULT_TARGET_FACES,
) -> BatchMeshPostprocessResult:
    instance_ids = [
        instance_id_for_content_hash(h) for h in _content_hashes_for_batch(session, batch_id)
    ]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )
    results = [
        postprocess_mesh_series(
            session, silver_client, settings, sid, preset, target_faces=target_faces
        )
        for sid in series_ids
        for preset in presets
    ]
    return BatchMeshPostprocessResult(
        batch_id=batch_id,
        series_results=results,
        processed_count=sum(1 for r in results if r.mesh_object_key is not None),
    )

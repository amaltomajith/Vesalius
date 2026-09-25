"""S9 -- Surface Extraction. See refs/STAGES.md S9, refs/GEOMETRY.md.

Obtains the intensity volume ONLY through `s7_deface.gate.open_defacing_gate` -- S7 is the hard
gate on S9 (STAGES.md's stage dependency graph). The S8 mask for the requested preset is read via
its `Artifact` row + object store, never by importing `s8_segment.service`/`segmentation`
(extends the existing static guard).

`skip_deface` exists only so TESTING.md Proof 2's literal call shape,
`extract_surface(series, skip_deface=True)`, has somewhere to go -- it is recorded in the report
for audit and NEVER read to change control flow. The gate is called unconditionally, every time.
A static test (`test_skip_deface_is_never_a_real_bypass`) asserts the gate call doesn't reference
it.

A gate refusal, or no S8 mask yet for the requested preset, is a `skipped` transform_run, never a
quarantine (S7/S8 precedent: the refusal is the correct outcome, not a series defect).
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
    DefacingGateError,
    QuarantineError,
    SurfaceGeometryMismatchError,
    SurfaceInputTamperedError,
    SurfaceModalityUnsupportedError,
    SurfaceOutputMismatchError,
    SurfaceUnreadableInputError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s6_volume_assembly.affine import parse_nifti_sform
from pipeline.stages.s7_deface import nifti_io as vol_io
from pipeline.stages.s7_deface.gate import GatedVolume, open_defacing_gate
from pipeline.stages.s8_segment import nifti_io as mask_io
from pipeline.stages.s8_segment.params import SUPPORTED_PRESETS
from pipeline.stages.s9_surface_extraction import mesh_io, params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s9_surface_extraction.models import BatchSurfaceResult, SeriesSurfaceResult
from pipeline.stages.s9_surface_extraction.report import build_report
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


@dataclass(frozen=True)
class _MaskRef:
    object_key: str
    sha256: str


def latest_s8_mask(session: Session, series_id: UUID, preset: str) -> _MaskRef | None:
    """The `mask` artifact of the latest SUCCESSFUL S8 run for `(series_id, preset)`. `preset`
    lives inside `parameters.algorithm.preset` (no dedicated column), matching how S7's gate
    already reads deface parameters out of a run's JSONB params."""
    runs = (
        session.execute(
            select(TransformRun)
            .where(
                TransformRun.stage == "S8",
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
                select(Artifact).where(Artifact.produced_by_run == run.id, Artifact.kind == "mask")
            ).scalar_one_or_none()
            if art is not None:
                return _MaskRef(object_key=art.object_key, sha256=art.sha256)
            return None
    return None


def _read_verified(client: Minio, settings: Settings, ref: _MaskRef) -> bytes:
    try:
        data = get_bytes(client, settings.minio_silver_bucket, ref.object_key)
    except Exception as exc:  # noqa: BLE001 -- infra failure, S6-S8 precedent: quarantine
        raise SurfaceUnreadableInputError(
            {"object_key": ref.object_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc
    actual = sha256_of_bytes(data)
    if actual != ref.sha256:
        raise SurfaceInputTamperedError(
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
    gated: GatedVolume,
    mask_ref: _MaskRef,
    mask_bytes: bytes,
    preset: str,
    skip_deface: bool,
    prefix: str,
) -> _Produced:
    try:
        meta = json.loads(gated.meta_bytes)
    except ValueError as exc:
        raise SurfaceUnreadableInputError({"error": str(exc)}) from exc

    intensity = meta.get("intensity", {})
    if meta.get("modality") != "CT" or not intensity.get("hu_semantics_valid"):
        raise SurfaceModalityUnsupportedError(
            {
                "modality": meta.get("modality"),
                "hu_semantics_valid": intensity.get("hu_semantics_valid"),
            }
        )

    try:
        loaded = vol_io.load_volume(gated.volume_bytes)
        mask_array = mask_io.load_mask(mask_bytes)
    except Exception as exc:  # noqa: BLE001 -- read/parse failure, not a surface verdict
        raise SurfaceUnreadableInputError({"error": f"{type(exc).__name__}: {exc}"}) from exc

    mask_sform = parse_nifti_sform(vol_io.header_bytes(mask_bytes))
    # geometry cross-check between the mask and the volume it was derived from -- extract_mesh
    # itself checks array shape once both are in hand; this checks the mask's OWN recorded
    # affine too, which extract_mesh has no way to see (it only receives one affine)
    if not np.allclose(mask_sform, loaded.sform, atol=params.AFFINE_TOLERANCE_MM):
        raise SurfaceGeometryMismatchError(
            {
                "reason": "mask_affine_disagrees_with_volume",
                "mask_sform": mask_sform.tolist(),
                "volume_sform": loaded.sform.tolist(),
            }
        )

    level = params.level_for(preset)
    result = extract_mesh(
        loaded.array,
        mask_array,
        loaded.sform,
        level=level,
        air_value=float(intensity["air_value"]),
        dilation_voxels=params.DILATION_VOXELS,
        step_size=params.STEP_SIZE,
        world_bbox_tolerance_mm=params.WORLD_BBOX_TOLERANCE_MM,
    )

    mesh_bytes = mesh_io.write_mesh(result.vertices_ras, result.faces)
    reread_verts, reread_faces = mesh_io.load_mesh(mesh_bytes)
    # PLY's binary vertex encoding is float32 (see mesh_io.py) -- a correct round trip is close,
    # not bit-exact; face indices are integers and are always compared exactly
    vertices_match = reread_verts.shape == result.vertices_ras.shape and np.allclose(
        reread_verts, result.vertices_ras, atol=params.VERTEX_ROUND_TRIP_TOLERANCE_MM
    )
    if not (vertices_match and np.array_equal(reread_faces, result.faces)):
        raise SurfaceOutputMismatchError(
            {
                "vertex_count_written": int(len(result.vertices_ras)),
                "vertex_count_reread": int(len(reread_verts)),
                "face_count_written": int(len(result.faces)),
                "face_count_reread": int(len(reread_faces)),
                "max_vertex_delta": (
                    float(np.abs(reread_verts - result.vertices_ras).max())
                    if reread_verts.shape == result.vertices_ras.shape
                    else None
                ),
            }
        )

    mesh_key = f"{prefix}/surface_{preset}.ply"
    report_key = f"{prefix}/surface_extraction_report.json"
    mesh_ref = {"object_key": mesh_key, "sha256": sha256_of_bytes(mesh_bytes)}
    report = build_report(
        series_id=gated.series_id,
        preset=preset,
        skip_deface=skip_deface,
        deface_block=gated.deface_block(),
        volume_ref={"object_key": gated.volume_key, "sha256": gated.volume_sha256},
        meta_ref={"sha256": gated.meta_sha256},
        mask_ref={"object_key": mask_ref.object_key, "sha256": mask_ref.sha256},
        mesh_ref=mesh_ref,
        report_key=report_key,
        stats=result.stats,
    )
    return _Produced(
        StageOutcome(output_hashes=[mesh_ref["sha256"], sha256_of_bytes(report)]),
        mesh_bytes,
        mesh_key,
        report,
        report_key,
        result.stats,
    )


def extract_surface(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    series_id: UUID,
    preset: str,
    *,
    skip_deface: bool = False,
) -> SeriesSurfaceResult:
    if preset not in SUPPORTED_PRESETS:
        raise ValueError(f"unsupported preset: {preset!r} (supported: {SUPPORTED_PRESETS})")
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    def _skip(reason: str, gate_code: str | None = None) -> SeriesSurfaceResult:
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={
                    "series_id": str(series_id),
                    "preset": preset,
                    "skip": reason,
                    **({"gate_code": gate_code} if gate_code else {}),
                },
            ),
            reason=reason if gate_code is None else f"{reason}:{gate_code}",
        )
        return SeriesSurfaceResult(
            series_id=series_id,
            preset=preset,
            skipped=True,
            skip_reason=reason if gate_code is None else f"{reason}:{gate_code}",
            gate_reason_code=gate_code,
        )

    # `skip_deface` is NEVER read here -- see module docstring and
    # test_skip_deface_is_never_a_real_bypass.
    try:
        gated = open_defacing_gate(session, silver_client, settings, series_id)
    except DefacingGateError as exc:
        return _skip("defacing_gate_refused", exc.reason_code)

    mask_ref = latest_s8_mask(session, series_id, preset)
    if mask_ref is None:
        return _skip("series_has_no_s8_mask")

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted([gated.volume_sha256, gated.meta_sha256, mask_ref.sha256]),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": params.algorithm_params(preset),
            "skip_deface": skip_deface,
            "deface": gated.deface_block(),
        },
    )
    prefix = f"{series_id}/surfaces/{idempotency_key(ctx)[:16]}"
    holder: list[_Produced] = []

    def _do() -> StageOutcome:
        mask_bytes = _read_verified(silver_client, settings, mask_ref)
        produced = _execute(
            gated=gated,
            mask_ref=mask_ref,
            mask_bytes=mask_bytes,
            preset=preset,
            skip_deface=skip_deface,
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
        return SeriesSurfaceResult(
            series_id=series_id, preset=preset, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        arts = {
            a.kind: a
            for a in session.execute(select(Artifact).where(Artifact.produced_by_run == run.id))
            .scalars()
            .all()
        }
        if "surface_mesh_raw" not in arts or "surface_extraction_report" not in arts:
            raise AssertionError(
                f"S9 transform_run {run.id} succeeded previously but artifact rows are missing "
                "-- idempotency invariant violated"
            )
        report_bytes = get_bytes(
            silver_client,
            settings.minio_silver_bucket,
            arts["surface_extraction_report"].object_key,
        )
        stats = json.loads(report_bytes)["stats"]
        return SeriesSurfaceResult(
            series_id=series_id,
            preset=preset,
            mesh_object_key=arts["surface_mesh_raw"].object_key,
            report_object_key=arts["surface_extraction_report"].object_key,
            vertex_count=stats["vertex_count"],
            face_count=stats["face_count"],
        )

    produced = holder[0]
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="surface_mesh_raw",
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
            kind="surface_extraction_report",
            format="json",
            zone="silver",
            object_key=produced.report_key,
            sha256=sha256_of_bytes(produced.report_bytes),
            bytes=len(produced.report_bytes),
            produced_by_run=run.id,
        )
    )
    session.flush()
    return SeriesSurfaceResult(
        series_id=series_id,
        preset=preset,
        mesh_object_key=produced.mesh_key,
        report_object_key=produced.report_key,
        vertex_count=produced.stats["vertex_count"],
        face_count=produced.stats["face_count"],
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors the identical helper in S2-S8 (consolidation deferred rather than touching
    verified code)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    return [row[0][0] for row in session.execute(stmt).all() if row[0]]


def extract_surface_batch(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    batch_id: UUID,
    presets: tuple[str, ...] = (params.DEFAULT_PRESET,),
) -> BatchSurfaceResult:
    instance_ids = [
        instance_id_for_content_hash(h) for h in _content_hashes_for_batch(session, batch_id)
    ]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )
    results = [
        extract_surface(session, silver_client, settings, sid, preset)
        for sid in series_ids
        for preset in presets
    ]
    return BatchSurfaceResult(
        batch_id=batch_id,
        series_results=results,
        extracted_count=sum(1 for r in results if r.mesh_object_key is not None),
    )

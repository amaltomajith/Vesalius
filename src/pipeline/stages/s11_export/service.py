"""S11 -- Export. See refs/STAGES.md S11, refs/GEOMETRY.md "GLB export"/"Manifest".

Obtains the intensity volume ONLY through `s7_deface.gate.open_defacing_gate` -- S11 is a
second consumer of the gate for the gold `volume.nii.gz` copy itself: S7's gate docstring
already named this deferral ("S8, S9 and S11 obtain volume bytes ONLY through
`open_defacing_gate`" -- a raw head CT NIfTI volume-renders to a face just as readily as a
mesh does). Reads S10's optimised mesh ONLY via its `Artifact` row + `s10_mesh_postprocess.
mesh_io`, never by importing `s10_mesh_postprocess.service`/`mesh_postprocess` (extends the
existing static guard to a fifth producer).

**Provenance walk, not a stored `preset` label.** S10's own `transform_runs.parameters` does
not record `preset` (a real gap found while building this stage -- S10 is already verified and
complete, so it is not retroactively touched). Rather than trust a `preset` string parsed back
out of an object key, `latest_s10_mesh` walks provenance PRECISELY: it follows S10's own
`input_hashes` to the exact S9 run that produced that mesh (by matching `surface_mesh_raw`
artifact sha256, not "the latest S9 run for this preset" -- S9 may have been re-run since), then
that S9 run's `parameters.algorithm.preset` for the actual preset, and likewise from S9's
`input_hashes` to the exact S8 run for `segment`'s own algorithm block. Every stage's
`algorithm_params()` block is carried forward into this run's own params (`source_parameters`)
VERBATIM, unmodified -- same discipline as the `deface`-carry-forward already three stages deep
(S7->S9->S10), extended one stage further.

Gold keys are FIXED and series-addressed (`gold/{series_id}/...`), not run-scoped like S7-S10's
silver keys -- DATA-MODEL.md's storage layout gives gold this shape specifically so Proof 3
("delete gold/{series_id}/, re-run from silver, compare hashes") holds across rebuilds. On
execute, the four gold objects are overwritten in place at the same keys, and each of the four
`Artifact` rows is UPSERTED (found by `(series_id, kind, zone='gold')`, updated in place) rather
than always inserted -- the first stage where "always insert" would leave `Artifact.sha256`
stale against the object actually at its key. Known, accepted limitation: gold holds ONE
canonical mesh export per series; re-running with a different preset overwrites the previous
preset's `surface.glb` (no per-preset gold key in the schema; S11 takes a single `preset`, not a
tuple like S8-S10).

No S10 mesh yet for the requested `(series, preset)`, or gate refusal, is a `skipped`
transform_run, never a quarantine (S7-S10 precedent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from minio import Minio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import (
    DefacingGateError,
    ExportInputTamperedError,
    ExportOutputMismatchError,
    ExportUnreadableInputError,
    QuarantineError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s7_deface.gate import GatedVolume, open_defacing_gate
from pipeline.stages.s8_segment.params import SUPPORTED_PRESETS
from pipeline.stages.s10_mesh_postprocess import mesh_io as optimized_mesh_io
from pipeline.stages.s11_export import glb_io, manifest, params, thumbnail
from pipeline.stages.s11_export.gltf_transform import MM_TO_M, to_gltf_space
from pipeline.stages.s11_export.models import BatchExportResult, SeriesExportResult
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


@dataclass(frozen=True)
class _MeshRef:
    object_key: str
    sha256: str
    deface_block: dict[str, Any]
    segment_params: dict[str, Any]
    surface_extraction_params: dict[str, Any]
    mesh_postprocess_params: dict[str, Any]


def _producing_run(
    session: Session, stage: str, kind: str, series_id: UUID, candidate_shas: list[str]
) -> TransformRun | None:
    """The successful run of `stage` that produced the `kind` artifact whose sha256 is among
    `candidate_shas` -- precise provenance, not "the latest run for this subject" (see module
    docstring). MUST be scoped to `series_id`, not just `kind`+sha256: different series can
    legitimately produce byte-identical mesh/mask content (e.g. two series built from the same
    fixture in a test run), so sha256 alone is not unique across the whole `artifacts` table --
    found live, against real Postgres, not assumed."""
    if not candidate_shas:
        return None
    art = session.execute(
        select(Artifact).where(
            Artifact.kind == kind,
            Artifact.series_id == series_id,
            Artifact.sha256.in_(candidate_shas),
        )
    ).scalar_one_or_none()
    if art is None:
        return None
    run = session.get(TransformRun, art.produced_by_run)
    if run is None or run.status != "success" or run.stage != stage:
        return None
    return run


def _params_block(run: TransformRun | None, key: str) -> dict[str, Any]:
    if run is None or not isinstance(run.parameters, dict):
        return {}
    block = run.parameters.get(key)
    return block if isinstance(block, dict) else {}


def latest_s10_mesh(session: Session, series_id: UUID, preset: str) -> _MeshRef | None:
    """The `surface_mesh_optimized` artifact of the latest SUCCESSFUL S10 run for
    `(series_id, preset)`, plus the deface/segment/surface_extraction/mesh_postprocess blocks
    carried forward for the manifest -- see module docstring for how `preset` is recovered."""
    runs = (
        session.execute(
            select(TransformRun)
            .where(
                TransformRun.stage == "S10",
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
        s9_run = _producing_run(session, "S9", "surface_mesh_raw", series_id, run.input_hashes)
        algo9 = _params_block(s9_run, "algorithm")
        if algo9.get("preset") != preset:
            continue
        art = session.execute(
            select(Artifact).where(
                Artifact.produced_by_run == run.id, Artifact.kind == "surface_mesh_optimized"
            )
        ).scalar_one_or_none()
        if art is None:
            continue
        s8_run = _producing_run(
            session, "S8", "mask", series_id, s9_run.input_hashes if s9_run else []
        )
        return _MeshRef(
            object_key=art.object_key,
            sha256=art.sha256,
            deface_block=_params_block(run, "deface"),
            segment_params=_params_block(s8_run, "algorithm"),
            surface_extraction_params=algo9,
            mesh_postprocess_params=_params_block(run, "algorithm"),
        )
    return None


def _read_verified_mesh(client: Minio, settings: Settings, ref: _MeshRef) -> bytes:
    try:
        data = get_bytes(client, settings.minio_silver_bucket, ref.object_key)
    except Exception as exc:  # noqa: BLE001 -- infra failure, S6-S10 precedent: quarantine
        raise ExportUnreadableInputError(
            {"object_key": ref.object_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc
    actual = sha256_of_bytes(data)
    if actual != ref.sha256:
        raise ExportInputTamperedError(
            {"object_key": ref.object_key, "catalog_sha256": ref.sha256, "actual_sha256": actual}
        )
    return data


@dataclass
class _Produced:
    outcome: StageOutcome
    glb_bytes: bytes
    glb_key: str
    volume_bytes: bytes
    volume_key: str
    thumbnail_bytes: bytes
    thumbnail_key: str
    manifest_bytes: bytes
    manifest_key: str
    face_count: int


def _execute(
    *,
    gated: GatedVolume,
    mesh_ref: _MeshRef,
    raw_mesh_bytes: bytes,
    preset: str,
    series_id: UUID,
    prefix: str,
) -> _Produced:
    try:
        vertices_ras, faces, normals_ras = optimized_mesh_io.load_mesh(raw_mesh_bytes)
    except Exception as exc:  # noqa: BLE001 -- read/parse failure, not an export verdict
        raise ExportUnreadableInputError({"error": f"{type(exc).__name__}: {exc}"}) from exc

    transformed = to_gltf_space(vertices_ras, normals_ras)
    assert transformed.normals_gltf is not None  # normals_ras was always provided above
    glb_write = glb_io.write_glb(transformed.vertices_gltf_m, faces, transformed.normals_gltf)
    reread_verts, reread_faces, reread_normals, reread_watertight = glb_io.load_glb(glb_write.data)
    verts_match = reread_verts.shape == transformed.vertices_gltf_m.shape and np.allclose(
        reread_verts,
        transformed.vertices_gltf_m,
        atol=params.VERTEX_ROUND_TRIP_TOLERANCE_MM * MM_TO_M,
    )
    if not (
        verts_match
        and np.array_equal(reread_faces, faces)
        and reread_watertight == glb_write.is_watertight
    ):
        raise ExportOutputMismatchError(
            {
                "artifact": "surface.glb",
                "face_count_written": glb_write.face_count,
                "face_count_reread": int(len(reread_faces)),
                "watertight_written": glb_write.is_watertight,
                "watertight_reread": reread_watertight,
            }
        )

    volume_bytes = gated.volume_bytes  # never a bare silver read -- see module docstring

    thumb = thumbnail.render_thumbnail(gated.volume_bytes)
    thumb_bytes = thumb.png_bytes
    reread_thumb = np.asarray(Image.open(BytesIO(thumb_bytes)), dtype=np.uint8)
    if not np.array_equal(reread_thumb, thumb.array_uint8):
        raise ExportOutputMismatchError(
            {
                "artifact": "thumbnail.png",
                "shape_written": list(thumb.array_uint8.shape),
                "shape_reread": list(reread_thumb.shape),
            }
        )

    glb_key = f"{prefix}/surface.glb"
    volume_key = f"{prefix}/volume.nii.gz"
    thumb_key = f"{prefix}/thumbnail.png"
    manifest_key = f"{prefix}/manifest.json"

    meta = json.loads(gated.meta_bytes)
    voxel_spacing_mm = meta.get("spatial", {}).get("spacing_mm", [None, None, None])

    manifest_bytes = manifest.build_manifest(
        contract_version=params.CONTRACT_VERSION,
        pipeline_version=params.PIPELINE_VERSION,
        series_id=series_id,
        preset=preset,
        glb_ref={
            "sha256": sha256_of_bytes(glb_write.data),
            "bytes": len(glb_write.data),
            "faces": glb_write.face_count,
        },
        volume_ref={"sha256": sha256_of_bytes(volume_bytes), "bytes": len(volume_bytes)},
        thumbnail_ref={"sha256": sha256_of_bytes(thumb_bytes), "bytes": len(thumb_bytes)},
        centring_offset_mm=[float(x) for x in transformed.centring_offset_mm],
        voxel_spacing_mm=list(voxel_spacing_mm),
        segment_params=mesh_ref.segment_params,
        surface_extraction_params=mesh_ref.surface_extraction_params,
        mesh_postprocess_params=mesh_ref.mesh_postprocess_params,
        deface_block=gated.deface_block(),
    )

    return _Produced(
        outcome=StageOutcome(
            output_hashes=[
                sha256_of_bytes(glb_write.data),
                sha256_of_bytes(volume_bytes),
                sha256_of_bytes(thumb_bytes),
                sha256_of_bytes(manifest_bytes),
            ]
        ),
        glb_bytes=glb_write.data,
        glb_key=glb_key,
        volume_bytes=volume_bytes,
        volume_key=volume_key,
        thumbnail_bytes=thumb_bytes,
        thumbnail_key=thumb_key,
        manifest_bytes=manifest_bytes,
        manifest_key=manifest_key,
        face_count=glb_write.face_count,
    )


def _upsert_gold_artifact(
    session: Session,
    *,
    series_id: UUID,
    kind: str,
    format_: str,
    object_key: str,
    data: bytes,
    run_id: UUID,
    contract_version: str | None,
) -> None:
    existing = session.execute(
        select(Artifact).where(
            Artifact.series_id == series_id, Artifact.kind == kind, Artifact.zone == "gold"
        )
    ).scalar_one_or_none()
    sha = sha256_of_bytes(data)
    if existing is not None:
        existing.format = format_
        existing.object_key = object_key
        existing.sha256 = sha
        existing.bytes = len(data)
        existing.contract_version = contract_version
        existing.produced_by_run = run_id
    else:
        session.add(
            Artifact(
                id=uuid4(),
                series_id=series_id,
                kind=kind,
                format=format_,
                zone="gold",
                object_key=object_key,
                sha256=sha,
                bytes=len(data),
                contract_version=contract_version,
                produced_by_run=run_id,
            )
        )


def export_series(
    session: Session,
    silver_client: Minio,
    gold_client: Minio,
    settings: Settings,
    series_id: UUID,
    preset: str = params.DEFAULT_PRESET,
) -> SeriesExportResult:
    if preset not in SUPPORTED_PRESETS:
        raise ValueError(f"unsupported preset: {preset!r} (supported: {SUPPORTED_PRESETS})")
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    def _skip(reason: str, gate_code: str | None = None) -> SeriesExportResult:
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
        return SeriesExportResult(
            series_id=series_id,
            preset=preset,
            skipped=True,
            skip_reason=reason if gate_code is None else f"{reason}:{gate_code}",
            gate_reason_code=gate_code,
        )

    try:
        gated = open_defacing_gate(session, silver_client, settings, series_id)
    except DefacingGateError as exc:
        return _skip("defacing_gate_refused", exc.reason_code)

    mesh_ref = latest_s10_mesh(session, series_id, preset)
    if mesh_ref is None:
        return _skip("series_has_no_s10_mesh")

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted([mesh_ref.sha256, gated.volume_sha256, gated.meta_sha256]),
        params={
            "series_id": str(series_id),
            "preset": preset,
            "params_version": params.PARAMS_VERSION,
            "algorithm": params.algorithm_params(),
            "deface": gated.deface_block(),
            "source_parameters": {
                "segment": mesh_ref.segment_params,
                "surface_extraction": mesh_ref.surface_extraction_params,
                "mesh_postprocess": mesh_ref.mesh_postprocess_params,
            },
        },
    )
    prefix = str(series_id)
    holder: list[_Produced] = []

    def _do() -> StageOutcome:
        raw_mesh_bytes = _read_verified_mesh(silver_client, settings, mesh_ref)
        produced = _execute(
            gated=gated,
            mesh_ref=mesh_ref,
            raw_mesh_bytes=raw_mesh_bytes,
            preset=preset,
            series_id=series_id,
            prefix=prefix,
        )
        put_bytes(
            gold_client,
            settings.minio_gold_bucket,
            produced.glb_key,
            produced.glb_bytes,
            "model/gltf-binary",
        )
        put_bytes(
            gold_client,
            settings.minio_gold_bucket,
            produced.volume_key,
            produced.volume_bytes,
            "application/gzip",
        )
        put_bytes(
            gold_client,
            settings.minio_gold_bucket,
            produced.thumbnail_key,
            produced.thumbnail_bytes,
            "image/png",
        )
        put_bytes(
            gold_client,
            settings.minio_gold_bucket,
            produced.manifest_key,
            produced.manifest_bytes,
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
        return SeriesExportResult(
            series_id=series_id, preset=preset, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        arts = {
            a.kind: a
            for a in session.execute(
                select(Artifact).where(Artifact.produced_by_run == run.id, Artifact.zone == "gold")
            )
            .scalars()
            .all()
        }
        required = {"surface_glb", "volume_nifti", "thumbnail", "manifest"}
        if not required.issubset(arts):
            raise AssertionError(
                f"S11 transform_run {run.id} succeeded previously but gold artifact rows are "
                "missing -- idempotency invariant violated"
            )
        manifest_bytes = get_bytes(
            gold_client, settings.minio_gold_bucket, arts["manifest"].object_key
        )
        face_count = json.loads(manifest_bytes)["artifacts"]["surface.glb"]["faces"]
        return SeriesExportResult(
            series_id=series_id,
            preset=preset,
            glb_object_key=arts["surface_glb"].object_key,
            volume_object_key=arts["volume_nifti"].object_key,
            thumbnail_object_key=arts["thumbnail"].object_key,
            manifest_object_key=arts["manifest"].object_key,
            face_count=face_count,
        )

    produced = holder[0]
    _upsert_gold_artifact(
        session,
        series_id=series_id,
        kind="surface_glb",
        format_="glb",
        object_key=produced.glb_key,
        data=produced.glb_bytes,
        run_id=run.id,
        contract_version="1.0",
    )
    _upsert_gold_artifact(
        session,
        series_id=series_id,
        kind="volume_nifti",
        format_="nifti-1-gzip",
        object_key=produced.volume_key,
        data=produced.volume_bytes,
        run_id=run.id,
        contract_version="1.0",
    )
    _upsert_gold_artifact(
        session,
        series_id=series_id,
        kind="thumbnail",
        format_="png",
        object_key=produced.thumbnail_key,
        data=produced.thumbnail_bytes,
        run_id=run.id,
        contract_version=None,
    )
    _upsert_gold_artifact(
        session,
        series_id=series_id,
        kind="manifest",
        format_="json",
        object_key=produced.manifest_key,
        data=produced.manifest_bytes,
        run_id=run.id,
        contract_version=None,
    )
    session.flush()
    return SeriesExportResult(
        series_id=series_id,
        preset=preset,
        glb_object_key=produced.glb_key,
        volume_object_key=produced.volume_key,
        thumbnail_object_key=produced.thumbnail_key,
        manifest_object_key=produced.manifest_key,
        face_count=produced.face_count,
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors the identical helper in S2-S10 (consolidation deferred rather than touching
    already-verified code)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    return [row[0][0] for row in session.execute(stmt).all() if row[0]]


def export_batch(
    session: Session,
    silver_client: Minio,
    gold_client: Minio,
    settings: Settings,
    batch_id: UUID,
    preset: str = params.DEFAULT_PRESET,
) -> BatchExportResult:
    instance_ids = [
        instance_id_for_content_hash(h) for h in _content_hashes_for_batch(session, batch_id)
    ]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )
    results = [
        export_series(session, silver_client, gold_client, settings, sid, preset)
        for sid in series_ids
    ]
    return BatchExportResult(
        batch_id=batch_id,
        series_results=results,
        exported_count=sum(1 for r in results if r.manifest_object_key is not None),
    )

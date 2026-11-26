"""S10 `structures` mode -- per-structure clean-up, smoothing and decimation under per-group face
budgets (refs/INTEGRATION.md §2, S10).

Like the threshold S10 this stage does not open the defacing gate: its only input is S9's
structures output, which exists only if the gate let S9 run, and S9's deface block is carried
forward. Inputs are read by catalog row and re-hashed.

Writes to silver under `{series_id}/structure_optimized/{key16}/`: `structures_optimized.npz`
(kind `structure_meshes_optimized`, with normals) and `structure_postprocess_report.json`
(kind `structure_postprocess_report`).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from minio import Minio
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    idempotency_key,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Quarantine, Series
from pipeline.catalog.structures_lookup import (
    artifacts_of_run,
    latest_structures_artifacts,
    read_verified,
)
from pipeline.common.config import Settings
from pipeline.common.errors import QuarantineError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s9_surface_extraction import structure_mesh_io as sm_io
from pipeline.stages.s10_mesh_postprocess import params
from pipeline.stages.s10_mesh_postprocess import structures_budget as sb
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


class SeriesStructurePostprocessResult(BaseModel):
    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    meshes_object_key: str | None = None
    report_object_key: str | None = None
    final_face_count: int | None = None


def postprocess_structures(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID
) -> SeriesStructurePostprocessResult:
    if session.get(Series, series_id) is None:
        raise ValueError(f"no series with id {series_id}")
    found = latest_structures_artifacts(
        session, "S9", series_id, ("structure_meshes_raw", "structure_surface_report")
    )
    if found is None:
        reason = "series_has_no_s9_structures"
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id), "mode": "structures", "skip": reason},
            ),
            reason=reason,
        )
        return SeriesStructurePostprocessResult(
            series_id=series_id, skipped=True, skip_reason=reason
        )
    _, arts = found
    report_in: dict[str, Any] = json.loads(
        read_verified(silver_client, settings, arts["structure_surface_report"], stage=STAGE)
    )

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted(
            [arts["structure_meshes_raw"].sha256, arts["structure_surface_report"].sha256]
        ),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": sb.structures_algorithm_params(),
            "deface": report_in["deface"],
        },
    )
    prefix = f"{series_id}/structure_optimized/{idempotency_key(ctx)[:16]}"
    holder: list[tuple[bytes, bytes, int]] = []

    def _do() -> StageOutcome:
        meshes = sm_io.unpack(
            read_verified(silver_client, settings, arts["structure_meshes_raw"], stage=STAGE)
        )
        by_node = {str(s["node"]): s for s in report_in["structures"]}
        raw = {m.node: int(len(m.faces)) for m in meshes}
        targets = sb.allocate(raw, {n: str(by_node[n]["group"]) for n in raw})
        out_meshes: list[sm_io.StructureMesh] = []
        normals: dict[str, Any] = {}
        structures = []
        for m in meshes:
            v, f, n, stats = sb.process_structure(m.vertices_ras, m.faces, targets[m.node])
            out_meshes.append(sm_io.StructureMesh(node=m.node, vertices_ras=v, faces=f))
            normals[m.node] = n
            structures.append(
                {**by_node[m.node], "faces": int(len(f)), "target_faces": targets[m.node], **stats}
            )
        packed = sm_io.pack(out_meshes, normals)
        group_faces: dict[str, int] = {}
        for s in structures:
            group_faces[str(s["group"])] = group_faces.get(str(s["group"]), 0) + int(s["faces"])
        report = json.dumps(
            {
                "series_id": str(series_id),
                "mode": "structures",
                "algorithm": sb.structures_algorithm_params(),
                "segmentation": report_in["segmentation"],
                "surface_extraction": report_in["algorithm"],
                "deface": report_in["deface"],
                "face_cut": report_in.get("face_cut", {"applied": False}),
                "voxel_spacing_mm": report_in["voxel_spacing_mm"],
                "meshes": {
                    "object_key": f"{prefix}/structures_optimized.npz",
                    "sha256": sha256_of_bytes(packed),
                },
                "group_faces": group_faces,
                "structures": sorted(structures, key=lambda s: str(s["node"])),
                "not_meshed": report_in["not_meshed"],
            },
            sort_keys=True,
            indent=1,
        ).encode()
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            f"{prefix}/structures_optimized.npz",
            packed,
            "application/zip",
        )
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            f"{prefix}/structure_postprocess_report.json",
            report,
            "application/json",
        )
        holder.append((packed, report, sum(group_faces.values())))
        return StageOutcome(output_hashes=[sha256_of_bytes(packed), sha256_of_bytes(report)])

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
        return SeriesStructurePostprocessResult(
            series_id=series_id, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        prev = artifacts_of_run(session, run.id)
        if "structure_meshes_optimized" not in prev or "structure_postprocess_report" not in prev:
            raise AssertionError(
                f"S10 structures run {run.id} succeeded but artifact rows are missing"
            )
        rep = json.loads(
            get_bytes(
                silver_client,
                settings.minio_silver_bucket,
                prev["structure_postprocess_report"].object_key,
            )
        )
        return SeriesStructurePostprocessResult(
            series_id=series_id,
            meshes_object_key=prev["structure_meshes_optimized"].object_key,
            report_object_key=prev["structure_postprocess_report"].object_key,
            final_face_count=sum(rep["group_faces"].values()),
        )

    packed, report, total_faces = holder[0]
    for kind, fmt, key, data in (
        ("structure_meshes_optimized", "npz", f"{prefix}/structures_optimized.npz", packed),
        (
            "structure_postprocess_report",
            "json",
            f"{prefix}/structure_postprocess_report.json",
            report,
        ),
    ):
        session.add(
            Artifact(
                id=uuid4(),
                series_id=series_id,
                kind=kind,
                format=fmt,
                zone="silver",
                object_key=key,
                sha256=sha256_of_bytes(data),
                bytes=len(data),
                produced_by_run=run.id,
            )
        )
    session.flush()
    return SeriesStructurePostprocessResult(
        series_id=series_id,
        meshes_object_key=f"{prefix}/structures_optimized.npz",
        report_object_key=f"{prefix}/structure_postprocess_report.json",
        final_face_count=total_faces,
    )

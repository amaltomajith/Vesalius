"""S9 `structures` mode -- one surface per S8 structure (refs/INTEGRATION.md §2, S9).

"S7 gates S9 exactly as before, no exceptions for structure mode": this service opens the
defacing gate first and records a `skipped` run on refusal, exactly like the threshold S9. Its
inputs are S8's structure labels + report, read from silver by the catalog row and re-hashed
(a changed file is quarantined, never used). Structures that yield no surface are listed in the
report, never dropped.

Writes to silver under `{series_id}/structure_surfaces/{key16}/`: `structures_raw.npz` (kind
`structure_meshes_raw`) and `structure_surface_report.json` (kind `structure_surface_report`),
which carries S8's per-structure measurements and the deface block forward.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from minio import Minio
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    idempotency_key,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Quarantine, Series
from pipeline.catalog.structures_lookup import latest_structures_artifacts, read_verified
from pipeline.common.config import Settings
from pipeline.common.errors import (
    DefacingGateError,
    QuarantineError,
    StructuresNoneMeshedError,
)
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface.gate import open_defacing_gate
from pipeline.stages.s9_surface_extraction import params
from pipeline.stages.s9_surface_extraction import structure_mesh_io as sm_io
from pipeline.stages.s9_surface_extraction import structure_meshing as sm
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


class SeriesStructureSurfacesResult(BaseModel):
    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    meshes_object_key: str | None = None
    report_object_key: str | None = None
    meshed_count: int | None = None
    not_meshed_count: int | None = None


def extract_structure_surfaces(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID
) -> SeriesStructureSurfacesResult:
    if session.get(Series, series_id) is None:
        raise ValueError(f"no series with id {series_id}")

    def _skip(reason: str, extra: dict[str, object]) -> SeriesStructureSurfacesResult:
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id), "mode": "structures", **extra},
            ),
            reason=reason,
        )
        return SeriesStructureSurfacesResult(series_id=series_id, skipped=True, skip_reason=reason)

    try:
        gated = open_defacing_gate(session, silver_client, settings, series_id)
    except DefacingGateError as exc:
        return _skip(
            f"defacing_gate_refused:{exc.reason_code}",
            {"skip": "defacing_gate_refused", "gate_code": exc.reason_code},
        )

    found = latest_structures_artifacts(
        session, "S8", series_id, ("structure_labels", "structures_report")
    )
    if found is None:
        return _skip("series_has_no_s8_structures", {"skip": "series_has_no_s8_structures"})
    _, arts = found

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted(
            [
                gated.volume_sha256,
                gated.meta_sha256,
                arts["structure_labels"].sha256,
                arts["structures_report"].sha256,
            ]
        ),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": sm.structures_algorithm_params(),
            "deface": gated.deface_block(),
        },
    )
    prefix = f"{series_id}/structure_surfaces/{idempotency_key(ctx)[:16]}"
    holder: list[tuple[bytes, bytes, int, int]] = []

    def _do() -> StageOutcome:
        import tempfile
        from pathlib import Path

        import numpy as np
        import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

        labels_bytes = read_verified(silver_client, settings, arts["structure_labels"], stage=STAGE)
        report_in: dict[str, Any] = json.loads(
            read_verified(silver_client, settings, arts["structures_report"], stage=STAGE)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.nii.gz"
            path.write_bytes(labels_bytes)
            image = sitk.ReadImage(str(path))
        labels = np.asarray(sitk.GetArrayFromImage(image)).astype(np.int32)
        meta = json.loads(gated.meta_bytes)
        affine = np.array(meta["spatial"]["affine"], dtype=float)
        meshes, not_meshed = sm.mesh_all(labels, affine, report_in["structures"])
        if not meshes:
            raise StructuresNoneMeshedError({"not_meshed": not_meshed})
        faces = {m.node: int(len(m.faces)) for m in meshes}
        structures = [
            {**s, "faces_raw": faces.get(str(s["node"]), 0)}
            for s in report_in["structures"]
            if str(s["node"]) in faces
        ]
        packed = sm_io.pack(meshes)
        report = json.dumps(
            {
                "series_id": str(series_id),
                "mode": "structures",
                "algorithm": sm.structures_algorithm_params(),
                "segmentation": {
                    "engine": report_in["engine"],
                    "label_set_version": report_in["label_set_version"],
                    "group_map_version": report_in["group_map_version"],
                },
                "deface": gated.deface_block(),
                "face_cut": report_in.get("face_cut", {"applied": False}),
                "voxel_spacing_mm": report_in["voxel_spacing_mm"],
                "meshes": {
                    "object_key": f"{prefix}/structures_raw.npz",
                    "sha256": sha256_of_bytes(packed),
                },
                "structures": structures,
                "not_meshed": not_meshed,
            },
            sort_keys=True,
            indent=1,
        ).encode()
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            f"{prefix}/structures_raw.npz",
            packed,
            "application/zip",
        )
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            f"{prefix}/structure_surface_report.json",
            report,
            "application/json",
        )
        holder.append((packed, report, len(meshes), len(not_meshed)))
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
        return SeriesStructureSurfacesResult(
            series_id=series_id, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        prev = {
            a.kind: a
            for a in session.execute(
                select(Artifact).where(Artifact.produced_by_run == run.id)
            ).scalars()
        }
        if "structure_meshes_raw" not in prev or "structure_surface_report" not in prev:
            raise AssertionError(
                f"S9 structures run {run.id} succeeded but artifact rows are missing"
            )
        rep = json.loads(
            get_bytes(
                silver_client,
                settings.minio_silver_bucket,
                prev["structure_surface_report"].object_key,
            )
        )
        return SeriesStructureSurfacesResult(
            series_id=series_id,
            meshes_object_key=prev["structure_meshes_raw"].object_key,
            report_object_key=prev["structure_surface_report"].object_key,
            meshed_count=len(rep["structures"]),
            not_meshed_count=len(rep["not_meshed"]),
        )

    packed, report, n_meshed, n_not = holder[0]
    for kind, fmt, key, data in (
        ("structure_meshes_raw", "npz", f"{prefix}/structures_raw.npz", packed),
        ("structure_surface_report", "json", f"{prefix}/structure_surface_report.json", report),
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
    return SeriesStructureSurfacesResult(
        series_id=series_id,
        meshes_object_key=f"{prefix}/structures_raw.npz",
        report_object_key=f"{prefix}/structure_surface_report.json",
        meshed_count=n_meshed,
        not_meshed_count=n_not,
    )

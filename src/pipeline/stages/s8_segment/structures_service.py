"""S8 `structures` mode -- anatomical segmentation into named structures (refs/INTEGRATION.md §2).

Same governance as the threshold mode (service.py), deliberately mirrored rather than shared so
the verified threshold path is untouched:

* volume bytes come ONLY through `open_defacing_gate`; a refusal is a `skipped` run, never a
  quarantine (the refusal is the correct outcome);
* CT only (HU semantics), like every HU-based S8 path;
* the engine's identity (version + weights hash) is part of the idempotency key, so a different
  model can never be mistaken for a cached result;
* the label volume must sit on the gated volume's exact grid, or the series is quarantined;
* every label found is kept -- unmapped ones go to the `other` group, never dropped.

Writes to silver under `{series_id}/structures/{key16}/`: `labels.nii.gz` (kind
`structure_labels`) and `structures_report.json` (kind `structures_report`, with the per-structure
measurements that later stages carry forward to the gold manifest).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library
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
from pipeline.catalog.models import Artifact, Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import (
    DefacingGateError,
    QuarantineError,
    SegmentModalityUnsupportedError,
    SegmentUnreadableInputError,
    StructuresEmptyError,
    StructuresGeometryMismatchError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s7_deface import nifti_io as vol_io
from pipeline.stages.s7_deface.gate import GatedVolume, open_defacing_gate
from pipeline.stages.s8_segment import params
from pipeline.stages.s8_segment import structures_params as sp
from pipeline.stages.s8_segment.structures import (
    SegmentationEngine,
    TotalSegmentatorEngine,
    cut_face_region,
    measure_structures,
)
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION
GEOMETRY_TOLERANCE = 1e-3


class SeriesStructuresResult(BaseModel):
    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    gate_reason_code: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    labels_object_key: str | None = None
    report_object_key: str | None = None
    structure_count: int | None = None


class BatchStructuresResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesStructuresResult]


def write_labels(source: sitk.Image, labels: np.ndarray) -> bytes:
    """uint8 label volume (TotalSegmentator `total` ids fit) on `source`'s exact geometry."""
    import tempfile
    from pathlib import Path

    if int(labels.max(initial=0)) > 255:
        raise ValueError("label ids above 255 do not fit uint8")
    image = sitk.GetImageFromArray(np.ascontiguousarray(labels, dtype=np.uint8))
    image.CopyInformation(source)
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "labels.nii.gz")
        sitk.WriteImage(image, path, useCompression=True)
        return Path(path).read_bytes()


def _execute(
    *, gated: GatedVolume, engine: SegmentationEngine, engine_meta: dict[str, object], prefix: str
) -> tuple[StageOutcome, bytes, str, bytes, str, int]:
    try:
        meta = json.loads(gated.meta_bytes)
    except ValueError as exc:
        raise SegmentUnreadableInputError({"error": str(exc)}) from exc
    intensity = meta.get("intensity", {})
    if meta.get("modality") != "CT" or not intensity.get("hu_semantics_valid"):
        raise SegmentModalityUnsupportedError(
            {
                "modality": meta.get("modality"),
                "hu_semantics_valid": intensity.get("hu_semantics_valid"),
            }
        )
    try:
        loaded = vol_io.load_volume(gated.volume_bytes)
    except Exception as exc:  # noqa: BLE001 -- read/parse failure, not a segmentation verdict
        raise SegmentUnreadableInputError(
            {"object_key": gated.volume_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc

    out = engine.segment(gated.volume_bytes)
    src = loaded.image
    origin, spacing, direction = out.geometry
    same_grid = (
        out.labels.shape == loaded.array.shape
        and np.allclose(origin, src.GetOrigin(), atol=GEOMETRY_TOLERANCE)
        and np.allclose(spacing, src.GetSpacing(), atol=GEOMETRY_TOLERANCE)
        and np.allclose(direction, src.GetDirection(), atol=GEOMETRY_TOLERANCE)
    )
    if not same_grid:
        raise StructuresGeometryMismatchError(
            {
                "volume_shape": list(loaded.array.shape),
                "labels_shape": list(out.labels.shape),
                "volume_origin": list(src.GetOrigin()),
                "labels_origin": list(origin),
            }
        )

    labels = out.labels
    face_cut: dict[str, object] = {"applied": False}
    if gated.face_region is not None:
        labels, removed = cut_face_region(
            labels, loaded.sform, gated.face_region["a_min"], gated.face_region["s_min"]
        )
        face_cut = {
            "applied": True,
            "method": sp.FACE_CUT,
            "region_ras_mm": gated.face_region,
            "voxels_removed": {
                out.names.get(k, f"label_{k}"): v for k, v in sorted(removed.items())
            },
        }
    elif gated.deface_block()["required"]:
        # fail closed: a head that needed defacing must come with S7's region
        raise StructuresGeometryMismatchError({"reason": "face_region_missing_for_defaced_study"})
    structures = measure_structures(labels, loaded.array, loaded.sform, out.names)
    if not structures:
        raise StructuresEmptyError({"volume_shape": list(loaded.array.shape)})

    labels_bytes = write_labels(src, labels)
    labels_key = f"{prefix}/labels.nii.gz"
    report_key = f"{prefix}/structures_report.json"
    groups: dict[str, int] = {}
    for s in structures:
        groups[str(s["group"])] = groups.get(str(s["group"]), 0) + 1
    report = json.dumps(
        {
            "series_id": str(gated.series_id),
            "mode": sp.MODE,
            "engine": engine_meta,
            "label_set_version": sp.LABEL_SET_VERSION,
            "group_map_version": sp.GROUP_MAP_VERSION,
            "deface": gated.deface_block(),
            "face_cut": face_cut,
            "volume": {"object_key": gated.volume_key, "sha256": gated.volume_sha256},
            "labels": {"object_key": labels_key, "sha256": sha256_of_bytes(labels_bytes)},
            "voxel_spacing_mm": [float(v) for v in src.GetSpacing()],
            "structure_count": len(structures),
            "group_counts": groups,
            "structures": structures,
        },
        sort_keys=True,
        indent=1,
    ).encode()
    outcome = StageOutcome(output_hashes=[sha256_of_bytes(labels_bytes), sha256_of_bytes(report)])
    return outcome, labels_bytes, labels_key, report, report_key, len(structures)


def segment_structures_series(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    series_id: UUID,
    *,
    engine: SegmentationEngine | None = None,
) -> SeriesStructuresResult:
    if session.get(Series, series_id) is None:
        raise ValueError(f"no series with id {series_id}")
    try:
        gated = open_defacing_gate(session, silver_client, settings, series_id)
    except DefacingGateError as exc:
        reason = f"defacing_gate_refused:{exc.reason_code}"
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
                    "mode": sp.MODE,
                    "skip": "defacing_gate_refused",
                    "gate_code": exc.reason_code,
                },
            ),
            reason=reason,
        )
        return SeriesStructuresResult(
            series_id=series_id, skipped=True, skip_reason=reason, gate_reason_code=exc.reason_code
        )

    engine = engine or TotalSegmentatorEngine()
    engine_meta = engine.describe()  # pinned version + weights hash, or SegmentationEngineError
    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted([gated.volume_sha256, gated.meta_sha256]),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": {**sp.algorithm_params(), "engine_identity": engine_meta},
            "deface": gated.deface_block(),
        },
    )
    prefix = f"{series_id}/structures/{idempotency_key(ctx)[:16]}"
    holder: list[tuple[StageOutcome, bytes, str, bytes, str, int]] = []

    def _do() -> StageOutcome:
        produced = _execute(gated=gated, engine=engine, engine_meta=engine_meta, prefix=prefix)
        _, labels_bytes, labels_key, report, report_key, _n = produced
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            labels_key,
            labels_bytes,
            "application/gzip",
        )
        put_bytes(
            silver_client, settings.minio_silver_bucket, report_key, report, "application/json"
        )
        holder.append(produced)
        return produced[0]

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
        return SeriesStructuresResult(
            series_id=series_id, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        arts = {
            a.kind: a
            for a in session.execute(
                select(Artifact).where(Artifact.produced_by_run == run.id)
            ).scalars()
        }
        if "structure_labels" not in arts or "structures_report" not in arts:
            raise AssertionError(
                f"S8 structures run {run.id} succeeded previously but artifact rows are missing"
            )
        report_obj: dict[str, Any] = json.loads(
            get_bytes(
                silver_client, settings.minio_silver_bucket, arts["structures_report"].object_key
            )
        )
        return SeriesStructuresResult(
            series_id=series_id,
            labels_object_key=arts["structure_labels"].object_key,
            report_object_key=arts["structures_report"].object_key,
            structure_count=int(report_obj["structure_count"]),
        )

    _, labels_bytes, labels_key, report, report_key, n = holder[0]
    for kind, fmt, key, data in (
        ("structure_labels", "nifti-1-gzip", labels_key, labels_bytes),
        ("structures_report", "json", report_key, report),
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
                contract_version="1.0" if kind == "structure_labels" else None,
                produced_by_run=run.id,
            )
        )
    session.flush()
    return SeriesStructuresResult(
        series_id=series_id,
        labels_object_key=labels_key,
        report_object_key=report_key,
        structure_count=n,
    )


def series_for_batch(session: Session, batch_id: UUID) -> Sequence[UUID]:
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    hashes = [row[0][0] for row in session.execute(stmt).all() if row[0]]
    ids = [instance_id_for_content_hash(h) for h in hashes]
    return (
        session.execute(select(Instance.series_id).where(Instance.id.in_(ids)).distinct())
        .scalars()
        .all()
    )


def segment_structures_batch(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    batch_id: UUID,
    *,
    engine: SegmentationEngine | None = None,
) -> BatchStructuresResult:
    results = [
        segment_structures_series(session, silver_client, settings, sid, engine=engine)
        for sid in series_for_batch(session, batch_id)
    ]
    return BatchStructuresResult(batch_id=batch_id, series_results=results)

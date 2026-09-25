"""S8 -- Segment. See refs/STAGES.md S8, refs/GEOMETRY.md.

Obtains volume bytes ONLY through `s7_deface.gate.open_defacing_gate` -- never reads a raw S6/S7
volume artifact by its own key or kind (enforced by the same static architecture guard S7
introduced; see tests/unit/test_gate_static_architecture.py). A gate refusal is not a series
defect (S7's design: the refusal itself is the correct outcome for e.g. a head with no S7 run
yet), so it is recorded as a `skipped` transform_run, never a quarantine, and the batch continues.

One call segments ONE series against ONE preset; several presets on a series are independent
runs (distinct idempotency keys, since `preset` is in `params`) sharing the same gated volume.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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
    EmptyMaskError,
    MaskTooLargeError,
    QuarantineError,
    SegmentGeometryMismatchError,
    SegmentModalityUnsupportedError,
    SegmentOutputHeaderMismatchError,
    SegmentUnreadableInputError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s6_volume_assembly.affine import parse_nifti_sform
from pipeline.stages.s7_deface import nifti_io as vol_io
from pipeline.stages.s7_deface.gate import GatedVolume, open_defacing_gate
from pipeline.stages.s8_segment import nifti_io as mask_io
from pipeline.stages.s8_segment import params
from pipeline.stages.s8_segment.models import BatchSegmentResult, SeriesSegmentResult
from pipeline.stages.s8_segment.report import build_report
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION


@dataclass
class _Produced:
    outcome: StageOutcome
    mask_bytes: bytes
    mask_key: str
    report_bytes: bytes
    report_key: str
    stats: dict[str, Any]


def _execute(*, gated: GatedVolume, preset: str, prefix: str) -> _Produced:
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

    meta_affine = np.array(meta["spatial"]["affine"], dtype=float)
    if list(loaded.array.shape) != list(meta["array"]["shape"]) or not np.allclose(
        meta_affine, loaded.sform, atol=1e-3
    ):
        raise SegmentGeometryMismatchError(
            {
                "meta_shape": meta["array"]["shape"],
                "volume_shape": list(loaded.array.shape),
                "meta_affine": meta_affine.tolist(),
                "sform": loaded.sform.tolist(),
            }
        )

    lo, hi = params.preset_bounds(preset)
    result = segment(
        loaded.array,
        lo=lo,
        hi=hi,
        closing_iterations=params.CLOSING_ITERATIONS,
        opening_iterations=params.OPENING_ITERATIONS,
        connectivity=params.CONNECTIVITY,
    )

    if result.stats["final_voxels"] == 0:
        raise EmptyMaskError({"preset": preset, **result.stats})
    if float(result.stats["coverage_fraction"]) > params.MAX_MASK_FRACTION:  # type: ignore[arg-type]
        raise MaskTooLargeError(
            {"preset": preset, "max_mask_fraction": params.MAX_MASK_FRACTION, **result.stats}
        )

    mask_bytes = mask_io.write_mask(loaded.image, result.mask)
    out_header = vol_io.header_bytes(mask_bytes)
    cmp = mask_io.compare_geometry(loaded.header, out_header, 1e-3)
    out_sform = parse_nifti_sform(out_header)
    sform_equal = bool(np.allclose(out_sform, loaded.sform, atol=1e-3))
    if not (cmp.equivalent and sform_equal):
        raise SegmentOutputHeaderMismatchError(
            {
                "dims_match": cmp.dims_match,
                "pixdim_close": cmp.pixdim_close,
                "codes_match": cmp.codes_match,
                "geometry_close": cmp.geometry_close,
                "sform_equal": sform_equal,
            }
        )
    reread = mask_io.load_mask(mask_bytes)
    if not np.array_equal(reread, result.mask):
        raise SegmentOutputHeaderMismatchError({"failure": "write_path_altered_voxels"})

    mask_key = f"{prefix}/mask_{preset}.nii.gz"
    report_key = f"{prefix}/segmentation_report.json"
    mask_ref = {"object_key": mask_key, "sha256": sha256_of_bytes(mask_bytes)}
    report = build_report(
        series_id=gated.series_id,
        preset=preset,
        deface_block=gated.deface_block(),
        volume_ref={"object_key": gated.volume_key, "sha256": gated.volume_sha256},
        meta_ref={"sha256": gated.meta_sha256},
        mask_ref=mask_ref,
        report_key=report_key,
        stats=result.stats,
        header_check={
            "dims_match": cmp.dims_match,
            "pixdim_close": cmp.pixdim_close,
            "codes_match": cmp.codes_match,
            "geometry_close": cmp.geometry_close,
            "sform_equal": sform_equal,
        },
    )
    return _Produced(
        StageOutcome(output_hashes=[mask_ref["sha256"], sha256_of_bytes(report)]),
        mask_bytes,
        mask_key,
        report,
        report_key,
        result.stats,
    )


def segment_series(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID, preset: str
) -> SeriesSegmentResult:
    if preset not in params.SUPPORTED_PRESETS:
        raise ValueError(f"unsupported preset: {preset!r} (supported: {params.SUPPORTED_PRESETS})")
    series = session.get(Series, series_id)
    if series is None:
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
                    "preset": preset,
                    "skip": "defacing_gate_refused",
                    "gate_code": exc.reason_code,
                },
            ),
            reason=reason,
        )
        return SeriesSegmentResult(
            series_id=series_id,
            preset=preset,
            skipped=True,
            skip_reason=reason,
            gate_reason_code=exc.reason_code,
        )

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted([gated.volume_sha256, gated.meta_sha256]),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": params.algorithm_params(preset),
            "deface": gated.deface_block(),
        },
    )
    prefix = f"{series_id}/masks/{idempotency_key(ctx)[:16]}"
    holder: list[_Produced] = []

    def _do() -> StageOutcome:
        produced = _execute(gated=gated, preset=preset, prefix=prefix)
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            produced.mask_key,
            produced.mask_bytes,
            "application/gzip",
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
        return SeriesSegmentResult(
            series_id=series_id, preset=preset, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        arts = {
            a.kind: a
            for a in session.execute(select(Artifact).where(Artifact.produced_by_run == run.id))
            .scalars()
            .all()
        }
        if "mask" not in arts or "segmentation_report" not in arts:
            raise AssertionError(
                f"S8 transform_run {run.id} succeeded previously but artifact rows are missing "
                "-- idempotency invariant violated"
            )
        # reconstruct the report-derived stats too, from the DB row's own catalogued object --
        # never from closure state -- so a rerun is equal to the first run, not a degraded one
        report_bytes = get_bytes(
            silver_client, settings.minio_silver_bucket, arts["segmentation_report"].object_key
        )
        stats = json.loads(report_bytes)["stats"]
        return SeriesSegmentResult(
            series_id=series_id,
            preset=preset,
            mask_object_key=arts["mask"].object_key,
            report_object_key=arts["segmentation_report"].object_key,
            final_voxels=stats["final_voxels"],
            coverage_fraction=stats["coverage_fraction"],
        )

    produced = holder[0]
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="mask",
            format="nifti-1-gzip",
            zone="silver",
            object_key=produced.mask_key,
            sha256=sha256_of_bytes(produced.mask_bytes),
            bytes=len(produced.mask_bytes),
            contract_version="1.0",
            produced_by_run=run.id,
        )
    )
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="segmentation_report",
            format="json",
            zone="silver",
            object_key=produced.report_key,
            sha256=sha256_of_bytes(produced.report_bytes),
            bytes=len(produced.report_bytes),
            produced_by_run=run.id,
        )
    )
    session.flush()
    return SeriesSegmentResult(
        series_id=series_id,
        preset=preset,
        mask_object_key=produced.mask_key,
        report_object_key=produced.report_key,
        final_voxels=produced.stats["final_voxels"],
        coverage_fraction=produced.stats["coverage_fraction"],
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors the identical helper in S2-S7 (consolidation deferred rather than touching
    verified code)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    return [row[0][0] for row in session.execute(stmt).all() if row[0]]


def segment_batch(
    session: Session,
    silver_client: Minio,
    settings: Settings,
    batch_id: UUID,
    presets: Sequence[str] = params.DEFAULT_PRESETS,
) -> BatchSegmentResult:
    instance_ids = [
        instance_id_for_content_hash(h) for h in _content_hashes_for_batch(session, batch_id)
    ]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )
    results = [
        segment_series(session, silver_client, settings, sid, preset)
        for sid in series_ids
        for preset in presets
    ]
    return BatchSegmentResult(
        batch_id=batch_id,
        series_results=results,
        segmented_count=sum(1 for r in results if r.mask_object_key is not None),
    )

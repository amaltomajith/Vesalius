"""S6 -- Volume Assembly. See refs/STAGES.md S6, refs/DICOM.md Part 2.

Reads verified de-identified silver instances (S5's output -- never bronze), sorts them by
position projection (never InstanceNumber), applies HU rescale, resamples to isotropic
spacing via SimpleITK, and writes silver/{series_id}/volume.nii.gz + meta.json.

Geometry is recomputed fresh from the actual silver bytes and cross-checked against S3's
persisted cache rather than trusted blindly -- this is explicitly the stage most likely to
be silently wrong (DICOM.md/STAGES.md), so an invariant "should" hold is exactly the thing
to verify, not assume.

Two things were verified empirically while building this (not trusted from the plan's
pseudocode, and one of them had to be corrected AGAIN after a first "fix" turned out wrong --
exactly the kind of silent bug this stage exists to catch):

1. `sitk.Image.SetDirection()` takes a row-major-flattened 3x3 matrix whose COLUMNS are the
   per-axis direction vectors -- naively concatenating (*row_cosine, *col_cosine, *normal)
   puts those vectors in the matrix's ROWS instead, which is the transpose of what's needed.
   The correct flat tuple interleaves component-wise; see `_direction_tuple()` below.
2. SimpleITK's physical space is LPS (same as DICOM) and its NIfTI writer converts to the
   file's RAS convention ITSELF. The image is therefore built with DICOM-native LPS
   Direction/Origin and NOT pre-flipped -- pre-flipping makes ITK flip a second time and
   leaves LPS-valued geometry in a header labelled RAS (a silent x/y mirror). An earlier
   version of this file did exactly that, and its sitk-write/sitk-read round-trip check
   passed anyway, because ITK's flip is symmetric on read. The check below therefore parses
   the sform out of the raw header bytes instead (`affine.read_nifti_sform`).
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np
import pydicom
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library
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
from pipeline.common.dicom_geometry import (
    MissingGeometryError,
    derive_slice_spacing,
    slice_position_from_values,
)
from pipeline.common.errors import (
    GeometryDriftError,
    OrientationMixedError,
    QuarantineError,
    S6UnreadableInstanceError,
    SpacingNonUniformError,
    TooFewSlicesError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s6_volume_assembly import affine as affine_mod
from pipeline.stages.s6_volume_assembly.hu_rescale import to_intensity_float32
from pipeline.stages.s6_volume_assembly.meta import build_meta_json
from pipeline.stages.s6_volume_assembly.models import BatchVolumeResult, SeriesVolumeResult
from pipeline.stages.s6_volume_assembly.params import (
    MIN_SLICES,
    ORIENTATION_TOLERANCE,
    PARAMS_VERSION,
    RESAMPLE_TARGET_SPACING_MM_V1,
    SPACING_DRIFT_TOLERANCE_MM,
)
from pipeline.stages.s6_volume_assembly.resampling import resample_isotropic
from pipeline.storage.object_store import get_bytes, put_bytes
from pipeline.version import PIPELINE_VERSION

STAGE = "S6"
STAGE_VERSION = "1.0.0"

_ROUND_TRIP_TOLERANCE = 1e-3


def s6_params() -> dict[str, object]:
    return {"params_version": PARAMS_VERSION, "target_spacing_mm": RESAMPLE_TARGET_SPACING_MM_V1}


def _silver_instance_key(series_id: UUID, pseudo_sop_uid: str) -> str:
    """Mirrors s5_verify.service._silver_instance_key's exact format. Not imported (that
    function is private-by-convention to its module) -- redefined here to avoid depending on
    a private name across a module boundary, matching this project's precedent of leaving
    small, stable, single-line duplicates alone rather than exporting internals.
    """
    return f"{series_id}/instances/{pseudo_sop_uid}.dcm"


@dataclass(frozen=True)
class _SilverSlice:
    instance: Instance
    artifact: Artifact
    dataset: pydicom.dataset.Dataset
    position: float


def _resolve_silver_artifacts(session: Session, series: Series) -> dict[UUID, Artifact] | None:
    """Returns {instance_id: Artifact} if every instance currently in the series has a
    successful S5 TransformRun AND a corresponding deid_instance_dicom Artifact; None if any
    instance is missing either. series.quality_status=='pass' alone does not guarantee this
    -- it is set once at S3 time and never revisited, so an individual instance can still
    have failed S4's OCR or S5's rescan inside an otherwise-passing series.
    """
    result: dict[UUID, Artifact] = {}
    for instance in series.instances:
        s5_success = session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S5",
                TransformRun.subject_type == "instance",
                TransformRun.subject_id == instance.id,
                TransformRun.status == "success",
            )
        ).scalar_one_or_none()
        if s5_success is None:
            return None
        expected_key = _silver_instance_key(series.id, instance.pseudo_sop_uid)
        artifact = session.execute(
            select(Artifact).where(
                Artifact.series_id == series.id,
                Artifact.kind == "deid_instance_dicom",
                Artifact.object_key == expected_key,
            )
        ).scalar_one_or_none()
        if artifact is None:
            return None
        result[instance.id] = artifact
    return result


def _gather_silver_slices(
    silver_client: Minio,
    settings: Settings,
    series: Series,
    artifacts_by_instance: dict[UUID, Artifact],
) -> list[_SilverSlice]:
    """One full read per silver instance (needs pixel data). Position is recomputed fresh
    from the actual bytes about to be assembled -- never taken from S2/S3's cached
    instances.slice_position -- so sorting later never depends on a cached value.
    """
    slices: list[_SilverSlice] = []
    for instance in series.instances:
        artifact = artifacts_by_instance[instance.id]
        try:
            data = get_bytes(silver_client, settings.minio_silver_bucket, artifact.object_key)
            ds = pydicom.dcmread(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001 -- infra failure, not a geometry verdict
            raise S6UnreadableInstanceError(
                {
                    "instance_id": str(instance.id),
                    "object_key": artifact.object_key,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ) from exc

        try:
            position = slice_position_from_values(
                getattr(ds, "ImageOrientationPatient", None),
                getattr(ds, "ImagePositionPatient", None),
            )
        except MissingGeometryError as exc:
            raise S6UnreadableInstanceError(
                {
                    "instance_id": str(instance.id),
                    "object_key": artifact.object_key,
                    "error": str(exc),
                }
            ) from exc

        slices.append(
            _SilverSlice(instance=instance, artifact=artifact, dataset=ds, position=position)
        )
    return slices


def _verify_geometry(series: Series, slices: list[_SilverSlice]) -> tuple[float, bool]:
    """Recompute-and-cross-check, not blind trust (S6 plan §3). Returns
    (fresh_slice_spacing, fresh_spacing_uniform) once every check passes."""
    if len(slices) < MIN_SLICES:
        raise TooFewSlicesError({"instance_count": len(slices), "minimum_required": MIN_SLICES})

    positions = [s.position for s in slices]
    fresh = derive_slice_spacing(positions)

    cached_spacing = series.slice_spacing
    cached_uniform = series.spacing_uniform
    spacing_matches = (
        cached_spacing is not None
        and fresh.slice_spacing is not None
        and abs(cached_spacing - fresh.slice_spacing) <= SPACING_DRIFT_TOLERANCE_MM
    )
    uniform_matches = cached_uniform == fresh.spacing_uniform
    if not (spacing_matches and uniform_matches):
        raise GeometryDriftError(
            {
                "series_id": str(series.id),
                "s3_cached_slice_spacing": cached_spacing,
                "s3_cached_spacing_uniform": cached_uniform,
                "s6_fresh_slice_spacing": fresh.slice_spacing,
                "s6_fresh_spacing_uniform": fresh.spacing_uniform,
            }
        )

    if not fresh.spacing_uniform:
        # Never resample across uneven slices as though they were even (CLAUDE.md rule 6).
        raise SpacingNonUniformError(
            {
                "series_id": str(series.id),
                "slice_spacing": fresh.slice_spacing,
                "max_deviation_ratio": fresh.max_deviation_ratio,
                "sorted_positions": fresh.sorted_positions,
            }
        )

    reference_iop = np.array(slices[0].dataset.ImageOrientationPatient, dtype=float)
    offenders = []
    for s in slices[1:]:
        iop = np.array(s.dataset.ImageOrientationPatient, dtype=float)
        delta = float(np.max(np.abs(iop - reference_iop)))
        if delta > ORIENTATION_TOLERANCE:
            offenders.append({"instance_id": str(s.instance.id), "max_deviation": delta})
    if offenders:
        raise OrientationMixedError({"reference": reference_iop.tolist(), "instances": offenders})

    assert fresh.slice_spacing is not None
    return fresh.slice_spacing, bool(fresh.spacing_uniform)


def _direction_tuple(
    row_cosine: np.ndarray, col_cosine: np.ndarray, normal: np.ndarray
) -> tuple[float, ...]:
    """sitk.Image.SetDirection()'s flat 9-tuple, row-major-flattened, whose COLUMNS are the
    per-axis direction vectors -- verified empirically (see module docstring). NOT the same
    as naively concatenating the three vectors, which puts them in the matrix's rows (the
    transpose of what SimpleITK expects) and silently produces a sheared/wrong geometry.
    """
    return (
        float(row_cosine[0]),
        float(col_cosine[0]),
        float(normal[0]),
        float(row_cosine[1]),
        float(col_cosine[1]),
        float(normal[1]),
        float(row_cosine[2]),
        float(col_cosine[2]),
        float(normal[2]),
    )


@dataclass
class _AssembledVolume:
    volume_bytes: bytes
    meta_bytes: bytes
    volume_key: str
    meta_key: str
    shape: tuple[int, int, int]


def _assemble(
    silver_client: Minio,
    settings: Settings,
    series: Series,
    artifacts_by_instance: dict[UUID, Artifact],
    run_id: UUID,
) -> _AssembledVolume:
    """Writes to MinIO but not the two Artifact rows themselves -- those are created by the
    caller after run_stage returns, matching S2/S3/S5's precedent. `run_id` IS the real
    transform_run id even though this function runs from inside run_stage's closure:
    run_stage flushes the TransformRun row (generating its id) before ever calling fn(), so
    the caller can look it up via idempotency_key(ctx) and pass it in here -- needed because
    meta.json's own content embeds `produced_by_run` (plan §6), not just the Artifact row.
    """
    raw_slices = _gather_silver_slices(silver_client, settings, series, artifacts_by_instance)
    slices = sorted(raw_slices, key=lambda s: s.position)

    fresh_slice_spacing, fresh_spacing_uniform = _verify_geometry(series, slices)

    first = slices[0].dataset
    iop = np.array(first.ImageOrientationPatient, dtype=float)
    row_cosine, col_cosine = iop[:3], iop[3:]
    normal = np.cross(row_cosine, col_cosine)
    origin = np.array(first.ImagePositionPatient, dtype=float)
    dy_orig, dx_orig = (float(v) for v in first.PixelSpacing)  # DICOM.md: [row, column]

    modality = series.modality
    default_pixel_value = -1024.0 if modality == "CT" else 0.0
    intensity_units = "hounsfield_units" if modality == "CT" else "arbitrary"
    hu_semantics_valid = modality == "CT"

    layers = []
    for s in slices:
        slope = float(getattr(s.dataset, "RescaleSlope", 1.0) or 1.0)
        intercept = float(getattr(s.dataset, "RescaleIntercept", 0.0) or 0.0)
        stored = s.dataset.pixel_array
        layers.append(to_intensity_float32(stored, slope, intercept))
    stack = np.stack(layers, axis=0)  # (Z, Y, X)

    # DICOM-native LPS geometry, deliberately NOT flipped to RAS: ITK's NIfTI writer performs
    # the LPS->RAS conversion itself (see module docstring + affine.py).
    image = sitk.GetImageFromArray(stack)
    image.SetDirection(_direction_tuple(row_cosine, col_cosine, normal))
    image.SetSpacing((dx_orig, dy_orig, fresh_slice_spacing))
    image.SetOrigin(tuple(float(v) for v in origin))

    resampled = resample_isotropic(image, RESAMPLE_TARGET_SPACING_MM_V1, default_pixel_value)

    affine_lps_matrix = affine_mod.affine_lps(
        row_cosine,
        col_cosine,
        normal,
        RESAMPLE_TARGET_SPACING_MM_V1,
        RESAMPLE_TARGET_SPACING_MM_V1,
        RESAMPLE_TARGET_SPACING_MM_V1,
        origin,
    )
    affine_ras_matrix = affine_mod.affine_ras(affine_lps_matrix)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = os.path.join(tmpdir, "volume.nii.gz")
        sitk.WriteImage(resampled, tmp_path)

        # Header verification (S6 plan §5.4, non-negotiable): parse the sform straight out of
        # the bytes actually written -- what a RAS-consuming tool will see -- and require it to
        # equal the independently computed affine_ras_matrix. Deliberately NOT a
        # sitk.ReadImage round trip: ITK converts LPS<->RAS symmetrically on read, so that
        # would pass even for a header that is wrong (an earlier version did exactly that).
        on_disk_sform = affine_mod.read_nifti_sform(tmp_path)
        if not np.allclose(on_disk_sform, affine_ras_matrix, atol=_ROUND_TRIP_TOLERANCE):
            raise AssertionError(
                "S6 header check failed -- the NIfTI sform actually written to disk does not "
                "match the independently computed RAS affine.\n"
                f"expected (affine_ras):\n{affine_ras_matrix}\n"
                f"actual (sform parsed from the written file):\n{on_disk_sform}"
            )

        with open(tmp_path, "rb") as fh:
            volume_bytes = fh.read()

    volume_shape = sitk.GetArrayFromImage(resampled).shape

    meta = build_meta_json(
        pipeline_version=PIPELINE_VERSION,
        stage_version=STAGE_VERSION,
        series_id=series.id,
        pseudo_series_uid=series.pseudo_series_uid,
        modality=modality,
        shape=(volume_shape[0], volume_shape[1], volume_shape[2]),
        affine_ras_matrix=affine_ras_matrix,
        target_spacing_mm=RESAMPLE_TARGET_SPACING_MM_V1,
        original_row_spacing_mm=dy_orig,
        original_column_spacing_mm=dx_orig,
        slice_spacing_mm=fresh_slice_spacing,
        dicom_slice_thickness_mm=(
            float(first.SliceThickness) if hasattr(first, "SliceThickness") else None
        ),
        dicom_spacing_between_slices_mm=(
            float(first.SpacingBetweenSlices) if hasattr(first, "SpacingBetweenSlices") else None
        ),
        params_version=PARAMS_VERSION,
        intensity_units=intensity_units,
        hu_semantics_valid=hu_semantics_valid,
        rescale_slope=float(getattr(first, "RescaleSlope", 1.0) or 1.0),
        rescale_intercept=float(getattr(first, "RescaleIntercept", 0.0) or 0.0),
        padding_value=default_pixel_value,
        instance_count=len(slices),
        spacing_uniform=fresh_spacing_uniform,
        orientation_consistent=True,
        slice_order_pseudo_sop_uids=[s.instance.pseudo_sop_uid for s in slices],
        produced_by_run=run_id,
    )
    meta_bytes = json.dumps(meta, sort_keys=True, indent=2).encode("utf-8")

    volume_key = f"{series.id}/volume.nii.gz"
    meta_key = f"{series.id}/meta.json"
    put_bytes(
        silver_client, settings.minio_silver_bucket, volume_key, volume_bytes, "application/gzip"
    )
    put_bytes(silver_client, settings.minio_silver_bucket, meta_key, meta_bytes, "application/json")

    return _AssembledVolume(
        volume_bytes=volume_bytes,
        meta_bytes=meta_bytes,
        volume_key=volume_key,
        meta_key=meta_key,
        shape=(volume_shape[0], volume_shape[1], volume_shape[2]),
    )


def assemble_volume_series(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID
) -> SeriesVolumeResult:
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    if series.quality_status != "pass":
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id)},
            ),
            reason="series_not_passed_quality_gate",
        )
        return SeriesVolumeResult(
            series_id=series_id, skipped=True, skip_reason="series_not_passed_quality_gate"
        )

    artifacts_by_instance = _resolve_silver_artifacts(session, series)
    if artifacts_by_instance is None:
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"reason": "not_verified", "series_id": str(series_id)},
            ),
            reason="series_not_fully_verified_by_s5",
        )
        return SeriesVolumeResult(
            series_id=series_id, skipped=True, skip_reason="series_not_fully_verified_by_s5"
        )

    input_hashes = sorted(a.sha256 for a in artifacts_by_instance.values())
    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=input_hashes,
        params=s6_params(),
    )

    assembled_holder: list[_AssembledVolume] = []

    def _do_assemble() -> StageOutcome:
        # run_stage flushes the TransformRun row (assigning its id) before calling fn(), so
        # the real id is already queryable here via the same idempotency_key -- needed
        # because meta.json's own content embeds produced_by_run (see _assemble's docstring).
        run_row = session.execute(
            select(TransformRun).where(TransformRun.idempotency_key == idempotency_key(ctx))
        ).scalar_one()
        assembled_holder.append(
            _assemble(silver_client, settings, series, artifacts_by_instance, run_id=run_row.id)
        )
        return StageOutcome(output_hashes=input_hashes)

    try:
        run, executed = run_stage(session, ctx, _do_assemble)
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
        return SeriesVolumeResult(
            series_id=series_id, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        volume_artifact = session.execute(
            select(Artifact).where(
                Artifact.produced_by_run == run.id, Artifact.kind == "volume_nifti"
            )
        ).scalar_one_or_none()
        meta_artifact = session.execute(
            select(Artifact).where(
                Artifact.produced_by_run == run.id, Artifact.kind == "volume_meta"
            )
        ).scalar_one_or_none()
        if volume_artifact is None or meta_artifact is None:
            raise AssertionError(
                f"S6 transform_run {run.id} succeeded previously but artifact rows are "
                "missing -- idempotency invariant violated"
            )
        return SeriesVolumeResult(
            series_id=series_id,
            volume_object_key=volume_artifact.object_key,
            meta_object_key=meta_artifact.object_key,
        )

    assembled = assembled_holder[0]
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series.id,
            kind="volume_nifti",
            format="nifti-1-gzip",
            zone="silver",
            object_key=assembled.volume_key,
            sha256=sha256_of_bytes(assembled.volume_bytes),
            bytes=len(assembled.volume_bytes),
            contract_version="1.0",
            produced_by_run=run.id,
        )
    )
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series.id,
            kind="volume_meta",
            format="json",
            zone="silver",
            object_key=assembled.meta_key,
            sha256=sha256_of_bytes(assembled.meta_bytes),
            bytes=len(assembled.meta_bytes),
            produced_by_run=run.id,
        )
    )
    session.flush()

    return SeriesVolumeResult(
        series_id=series_id,
        volume_object_key=assembled.volume_key,
        meta_object_key=assembled.meta_key,
        shape=assembled.shape,
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors s2/s3/s4's identical helper: resolved via S1's transform_runs, not
    instances.ingest_batch_id (confirmed never populated)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    rows = session.execute(stmt).all()
    return [row[0][0] for row in rows if row[0]]


def assemble_volume_batch(
    session: Session, silver_client: Minio, settings: Settings, batch_id: UUID
) -> BatchVolumeResult:
    content_hashes = _content_hashes_for_batch(session, batch_id)
    instance_ids = [instance_id_for_content_hash(h) for h in content_hashes]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )

    series_results = [
        assemble_volume_series(session, silver_client, settings, sid) for sid in series_ids
    ]
    assembled_count = sum(1 for r in series_results if r.volume_object_key is not None)
    return BatchVolumeResult(
        batch_id=batch_id, series_results=series_results, assembled_count=assembled_count
    )

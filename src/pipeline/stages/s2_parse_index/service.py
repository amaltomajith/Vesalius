"""S2 -- Parse & Index. See refs/STAGES.md S2.

Reads bronze instances landed by a completed S1 batch, indexes them with
`pydicom.dcmread(..., stop_before_pixels=True)`, groups instance -> series -> study, and
records original UIDs in `restricted.uid_map` (S4 replaces the provisional pseudo values
with its deterministic HMAC scheme once available).
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID, uuid4

import pydicom
from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, StageOutcome, run_stage
from pipeline.catalog.models import (
    Instance,
    PatientMap,
    Quarantine,
    Series,
    Study,
    TransformRun,
    UidMap,
)
from pipeline.catalog.provisional import PROVISIONAL_QUALITY_STATUS, provisional_pseudo_uid
from pipeline.common.config import Settings
from pipeline.common.errors import CorruptHeaderError, QuarantineError
from pipeline.common.hashing import instance_id_for_content_hash
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s2_parse_index.models import ParsedInstanceResult, ParseIndexBatchResult
from pipeline.stages.s2_parse_index.tags import extract_tags, resolve_deface_required
from pipeline.storage.bronze import bronze_key

STAGE = "S2"
STAGE_VERSION = "1.0.0"


def _get_or_create_uid_map(session: Session, original_uid: str, uid_type: str) -> str:
    existing = session.get(UidMap, original_uid)
    if existing is not None:
        return existing.pseudo_uid
    pseudo = provisional_pseudo_uid()
    session.add(UidMap(original_uid=original_uid, pseudo_uid=pseudo, uid_type=uid_type))
    session.flush()
    return pseudo


def _get_or_create_pseudo_patient_id(session: Session, original_patient_id: str | None) -> str:
    if original_patient_id is None:
        # Rare (PatientID is technically optional): no restricted.patient_map row to
        # anchor to, so mint a bare provisional pseudo id with no map entry.
        return provisional_pseudo_uid()
    existing = session.get(PatientMap, original_patient_id)
    if existing is not None:
        return existing.pseudo_patient_id
    pseudo = provisional_pseudo_uid()
    # date_offset_days is S4's HMAC-derived value (DICOM.md); 0 is a provisional
    # placeholder per the same convention as pseudo UIDs and quality_status.
    session.add(
        PatientMap(
            original_patient_id=original_patient_id,
            pseudo_patient_id=pseudo,
            date_offset_days=0,
        )
    )
    session.flush()
    return pseudo


def _find_or_create_study(session: Session, pseudo_study_uid: str, pseudo_patient_id: str) -> Study:
    existing = session.execute(
        select(Study).where(Study.pseudo_study_uid == pseudo_study_uid)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    study = Study(
        id=uuid4(),
        pseudo_study_uid=pseudo_study_uid,
        pseudo_patient_id=pseudo_patient_id,
        modality=None,
        body_part=None,
        body_part_source=None,
        shifted_study_date=None,
        # Starting point for the monotonic OR below, not a real fail-closed default --
        # resolve_deface_required() itself returns True for an unresolved body part, so
        # fail-closed behavior comes from that computation. Seeding this True would make
        # `study.deface_required or deface_required` always True regardless of the real
        # per-instance result (True or X is always True), silently defeating it.
        deface_required=False,
    )
    session.add(study)
    session.flush()
    return study


def _find_or_create_series(
    session: Session,
    pseudo_series_uid: str,
    study: Study,
    tags: dict[str, Any],
) -> Series:
    existing = session.execute(
        select(Series).where(Series.pseudo_series_uid == pseudo_series_uid)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    rows = tags["Rows"]
    columns = tags["Columns"]
    series = Series(
        id=uuid4(),
        study_id=study.id,
        pseudo_series_uid=pseudo_series_uid,
        modality=tags["Modality"] or "UNKNOWN",
        instance_count=0,
        rows=int(rows) if rows is not None else None,
        columns=int(columns) if columns is not None else None,
        pixel_spacing=tags["PixelSpacing"],
        slice_spacing=None,  # derived at S6, not S2 -- DICOM.md Part 2
        spacing_uniform=None,
        orientation=tags["ImageOrientationPatient"],
        rescale_slope=float(tags["RescaleSlope"]) if tags["RescaleSlope"] is not None else None,
        rescale_intercept=(
            float(tags["RescaleIntercept"]) if tags["RescaleIntercept"] is not None else None
        ),
        quality_status=PROVISIONAL_QUALITY_STATUS,
    )
    session.add(series)
    session.flush()
    return series


def _read_bronze_bytes(minio_client: Minio, settings: Settings, content_hash: str) -> bytes:
    key = bronze_key(content_hash)
    response = minio_client.get_object(settings.minio_bronze_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _parse_one(
    session: Session,
    minio_client: Minio,
    settings: Settings,
    content_hash: str,
) -> ParsedInstanceResult:
    subject_id = instance_id_for_content_hash(content_hash)
    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="instance",
        subject_id=subject_id,
        input_hashes=[content_hash],
        params={},
    )

    result_holder: dict[str, ParsedInstanceResult] = {}

    def _do_parse() -> StageOutcome:
        try:
            data = _read_bronze_bytes(minio_client, settings, content_hash)
            ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
        except Exception as exc:  # noqa: BLE001 - any parse failure quarantines
            raise CorruptHeaderError(
                {"content_hash": content_hash, "error": f"{type(exc).__name__}: {exc}"}
            ) from exc

        tags = extract_tags(ds)
        original_study_uid = tags["StudyInstanceUID"]
        original_series_uid = tags["SeriesInstanceUID"]
        original_sop_uid = tags["SOPInstanceUID"]
        if not original_study_uid or not original_series_uid or not original_sop_uid:
            raise CorruptHeaderError(
                {
                    "content_hash": content_hash,
                    "error": "missing StudyInstanceUID/SeriesInstanceUID/SOPInstanceUID",
                }
            )

        if tags["FrameOfReferenceUID"]:
            _get_or_create_uid_map(session, tags["FrameOfReferenceUID"], "frame_of_reference")

        pseudo_study_uid = _get_or_create_uid_map(session, original_study_uid, "study")
        pseudo_series_uid = _get_or_create_uid_map(session, original_series_uid, "series")
        pseudo_sop_uid = _get_or_create_uid_map(session, original_sop_uid, "sop")
        pseudo_patient_id = _get_or_create_pseudo_patient_id(session, tags["PatientID"])

        study = _find_or_create_study(session, pseudo_study_uid, pseudo_patient_id)
        series = _find_or_create_series(session, pseudo_series_uid, study, tags)

        deface_required, body_part_source, normalized_body_part = resolve_deface_required(
            tags["BodyPartExamined"]
        )
        study.modality = study.modality or tags["Modality"]
        study.body_part = study.body_part or normalized_body_part
        study.body_part_source = study.body_part_source or body_part_source
        # deface_required is monotonic: once any instance in the study requires it, the
        # whole study does; never downgrade an already-True study back to False.
        study.deface_required = study.deface_required or deface_required

        instance = Instance(
            id=subject_id,
            series_id=series.id,
            pseudo_sop_uid=pseudo_sop_uid,
            content_hash=content_hash,
            bronze_key=bronze_key(content_hash),
            slice_position=None,  # computed at S6 (DICOM.md Part 2), not S2
            burned_in_flag=tags["BurnedInAnnotation"],
        )
        session.add(instance)
        series.instance_count += 1
        session.flush()

        result_holder["result"] = ParsedInstanceResult(
            content_hash=content_hash,
            instance_id=subject_id,
            series_id=series.id,
            study_id=study.id,
            quarantined=False,
        )
        return StageOutcome(output_hashes=[content_hash])

    try:
        _run, executed = run_stage(session, ctx, _do_parse)
    except QuarantineError as exc:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="instance",
                subject_id=subject_id,
                stage=STAGE,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        return ParsedInstanceResult(
            content_hash=content_hash,
            instance_id=subject_id,
            series_id=None,
            study_id=None,
            quarantined=True,
            reason_code=exc.reason_code,
        )

    if not executed:
        # Idempotent re-run: a prior successful S2 run for this content already exists,
        # so _do_parse() was never called and result_holder was never populated. Rebuild
        # the result from the catalog row _do_parse() created last time instead.
        existing_instance = session.get(Instance, subject_id)
        if existing_instance is None:
            raise AssertionError(
                f"transform_run for {subject_id} succeeded previously but no Instance "
                "row exists -- idempotency invariant violated"
            )
        return ParsedInstanceResult(
            content_hash=content_hash,
            instance_id=subject_id,
            series_id=existing_instance.series_id,
            study_id=existing_instance.series.study_id,
            quarantined=False,
        )

    return result_holder["result"]


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Every distinct piece of content this batch touched, whether newly landed in
    bronze (status='success') or already present from an earlier batch
    (status='skipped', reason='duplicate_content') -- both belong to this batch and both
    need indexing.
    """
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    rows = session.execute(stmt).all()
    return [row[0][0] for row in rows if row[0]]


def parse_and_index_batch(
    session: Session,
    minio_client: Minio,
    settings: Settings,
    batch_id: UUID,
) -> ParseIndexBatchResult:
    content_hashes = _content_hashes_for_batch(session, batch_id)
    indexed: list[ParsedInstanceResult] = []
    quarantined_count = 0
    for content_hash in content_hashes:
        result = _parse_one(session, minio_client, settings, content_hash)
        indexed.append(result)
        if result.quarantined:
            quarantined_count += 1

    return ParseIndexBatchResult(
        batch_id=batch_id, indexed=indexed, quarantined_count=quarantined_count
    )

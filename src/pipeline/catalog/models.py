"""SQLAlchemy ORM models mirroring migrations/versions/000{1,2}_*.py. Migrations are the
source of truth for the actual DDL; this file must be kept in sync with them by hand.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ========================= restricted =========================


class UidMap(Base):
    __tablename__ = "uid_map"
    __table_args__ = {"schema": "restricted"}

    original_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    pseudo_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    uid_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class PatientMap(Base):
    __tablename__ = "patient_map"
    __table_args__ = {"schema": "restricted"}

    original_patient_id: Mapped[str] = mapped_column(Text, primary_key=True)
    pseudo_patient_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    date_offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DeidAudit(Base):
    __tablename__ = "deid_audit"
    __table_args__ = {"schema": "restricted"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    pixel_region: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


# =========================== public ===========================


class IngestBatch(Base):
    __tablename__ = "ingest_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pseudo_study_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    pseudo_patient_id: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_part: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_part_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    shifted_study_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deface_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    series: Mapped[list[Series]] = relationship(back_populates="study")


class Series(Base):
    __tablename__ = "series"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("studies.id"), nullable=False
    )
    pseudo_series_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    modality: Mapped[str] = mapped_column(Text, nullable=False)
    instance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pixel_spacing: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    slice_spacing: Mapped[float | None] = mapped_column(Float, nullable=True)
    spacing_uniform: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    orientation: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    rescale_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    rescale_intercept: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    study: Mapped[Study] = relationship(back_populates="series")
    instances: Mapped[list[Instance]] = relationship(back_populates="series")


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series.id"), nullable=False
    )
    pseudo_sop_uid: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    bronze_key: Mapped[str] = mapped_column(Text, nullable=False)
    slice_position: Mapped[float | None] = mapped_column(Float, nullable=True)
    burned_in_flag: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingest_batches.id"), nullable=True
    )

    series: Mapped[Series] = relationship(back_populates="instances")


class TransformRun(Base):
    __tablename__ = "transform_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    stage_version: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    input_hashes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    output_hashes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("series.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    zone: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    produced_by_run: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transform_runs.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LineageEdge(Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (
        CheckConstraint(
            "upstream_run IS NOT NULL OR upstream_artifact IS NOT NULL",
            name="ck_lineage_edges_upstream_present",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    downstream_run: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transform_runs.id"), nullable=False
    )
    upstream_run: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transform_runs.id"), nullable=True
    )
    upstream_artifact: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artifacts.id"), nullable=True
    )


class Quarantine(Base):
    __tablename__ = "quarantine"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class DataContract(Base):
    __tablename__ = "data_contracts"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    schema: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(server_default=func.now())

"""public schema: catalog tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "studies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("pseudo_study_uid", sa.Text(), nullable=False, unique=True),
        sa.Column("pseudo_patient_id", sa.Text(), nullable=False),
        sa.Column("modality", sa.Text(), nullable=True),
        sa.Column("body_part", sa.Text(), nullable=True),
        sa.Column("body_part_source", sa.Text(), nullable=True),
        sa.Column("shifted_study_date", sa.Date(), nullable=True),
        sa.Column("deface_required", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "series",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("studies.id"),
            nullable=False,
        ),
        sa.Column("pseudo_series_uid", sa.Text(), nullable=False, unique=True),
        sa.Column("modality", sa.Text(), nullable=False),
        sa.Column("instance_count", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("columns", sa.Integer(), nullable=True),
        sa.Column("pixel_spacing", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column("slice_spacing", sa.Float(), nullable=True),
        sa.Column("spacing_uniform", sa.Boolean(), nullable=True),
        sa.Column("orientation", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column("rescale_slope", sa.Float(), nullable=True),
        sa.Column("rescale_intercept", sa.Float(), nullable=True),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("series.id"),
            nullable=False,
        ),
        sa.Column("pseudo_sop_uid", sa.Text(), nullable=False, unique=True),
        sa.Column("content_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("bronze_key", sa.Text(), nullable=False),
        sa.Column("slice_position", sa.Float(), nullable=True),
        sa.Column("burned_in_flag", sa.Text(), nullable=True),
        sa.Column(
            "ingest_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingest_batches.id"),
            nullable=True,
        ),
    )

    op.create_table(
        "transform_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("stage_version", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("input_hashes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("output_hashes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_transform_runs_subject", "transform_runs", ["subject_id", "stage"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("series.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("zone", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=True),
        sa.Column(
            "produced_by_run",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transform_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "lineage_edges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "downstream_run",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transform_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "upstream_run",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transform_runs.id"),
            nullable=True,
        ),
        sa.Column(
            "upstream_artifact",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "upstream_run IS NOT NULL OR upstream_artifact IS NOT NULL",
            name="ck_lineage_edges_upstream_present",
        ),
    )

    op.create_table(
        "quarantine",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_type", sa.Text(), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_quarantine_reason_code", "quarantine", ["reason_code"])

    op.create_table(
        "data_contracts",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("version", sa.Text(), primary_key=True),
        sa.Column("schema", postgresql.JSONB(), nullable=False),
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("data_contracts")
    op.drop_index("ix_quarantine_reason_code", table_name="quarantine")
    op.drop_table("quarantine")
    op.drop_table("lineage_edges")
    op.drop_table("artifacts")
    op.drop_index("ix_transform_runs_subject", table_name="transform_runs")
    op.drop_table("transform_runs")
    op.drop_table("instances")
    op.drop_table("series")
    op.drop_table("studies")
    op.drop_table("ingest_batches")

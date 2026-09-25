"""restricted schema: uid_map, patient_map, deid_audit

Revision ID: 0001
Revises:
Create Date: 2026-09-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA restricted")

    op.create_table(
        "uid_map",
        sa.Column("original_uid", sa.Text(), primary_key=True),
        sa.Column("pseudo_uid", sa.Text(), nullable=False, unique=True),
        sa.Column("uid_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="restricted",
    )

    op.create_table(
        "patient_map",
        sa.Column("original_patient_id", sa.Text(), primary_key=True),
        sa.Column("pseudo_patient_id", sa.Text(), nullable=False, unique=True),
        sa.Column("date_offset_days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="restricted",
    )

    op.create_table(
        "deid_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=True),
        sa.Column("tag_name", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column(
            "pixel_region",
            postgresql.ARRAY(sa.Integer()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="restricted",
    )
    op.create_index(
        "ix_deid_audit_instance_id",
        "deid_audit",
        ["instance_id"],
        schema="restricted",
    )


def downgrade() -> None:
    op.drop_index("ix_deid_audit_instance_id", table_name="deid_audit", schema="restricted")
    op.drop_table("deid_audit", schema="restricted")
    op.drop_table("patient_map", schema="restricted")
    op.drop_table("uid_map", schema="restricted")
    op.execute("DROP SCHEMA restricted")

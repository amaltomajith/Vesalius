"""row-level security on restricted.* (defense in depth)

The schema has no tenant/row-level predicate to key on -- RLS here is not a real
per-row filter, it's an explicit, auditable (`pg_policies`) guard against a future
accidental GRANT on `restricted` to some other role. Only pipeline_svc is ever
granted a policy.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("uid_map", "patient_map", "deid_audit")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE restricted.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE restricted.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY pipeline_full_access ON restricted.{table}
            FOR ALL TO pipeline_svc USING (true) WITH CHECK (true)
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS pipeline_full_access ON restricted.{table}")
        op.execute(f"ALTER TABLE restricted.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE restricted.{table} DISABLE ROW LEVEL SECURITY")

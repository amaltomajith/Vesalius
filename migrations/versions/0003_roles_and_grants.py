"""roles and grants enforcing the PHI boundary

pipeline_svc: full access to restricted + public (the only role S1/S2 use).
serving_ro: SELECT on public catalog tables only -- no grant of any kind on the
restricted schema. The absence of a GRANT statement is the control; see
refs/DATA-MODEL.md's PHI boundary section.

Passwords come from PIPELINE_SVC_PASSWORD / SERVING_RO_PASSWORD and are never
hardcoded in this file. `CREATE ROLE ... PASSWORD` is DDL whose PASSWORD clause
takes a string literal in Postgres's grammar -- it does not accept a bind
parameter (`CREATE ROLE x PASSWORD $1` is a syntax error), so a driver-level
bindparam can't be used here. Instead each password is embedded via
dollar-quoting with a fresh random tag generated at migration time
(`$pw_<32 hex chars>$...$pw_<same hex chars>$`) -- Postgres treats everything
between the matching tags as a literal with zero escape processing, so no
quoting/escaping bugs are possible regardless of password content, and the
random per-run tag makes it infeasible for a password value to happen to
contain a closing delimiter.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15
"""
import os
import secrets
from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_role_sql(role: str, password: str) -> str:
    tag = f"pw_{secrets.token_hex(16)}"
    return f"CREATE ROLE {role} LOGIN PASSWORD ${tag}${password}${tag}$"


def upgrade() -> None:
    pipeline_svc_password = os.environ["PIPELINE_SVC_PASSWORD"]
    serving_ro_password = os.environ["SERVING_RO_PASSWORD"]

    op.execute(_create_role_sql("pipeline_svc", pipeline_svc_password))
    op.execute(_create_role_sql("serving_ro", serving_ro_password))

    op.execute("GRANT ALL PRIVILEGES ON SCHEMA restricted TO pipeline_svc")
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA restricted TO pipeline_svc")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA restricted GRANT ALL ON TABLES TO pipeline_svc"
    )
    op.execute("REVOKE ALL ON SCHEMA restricted FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA restricted FROM PUBLIC")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA restricted REVOKE ALL ON TABLES FROM PUBLIC"
    )
    # serving_ro: deliberately no GRANT statement anywhere referencing `restricted`.

    op.execute("GRANT USAGE ON SCHEMA public TO pipeline_svc, serving_ro")
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO pipeline_svc")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO pipeline_svc"
    )
    op.execute(
        """
        GRANT SELECT ON
            studies, series, instances, artifacts, lineage_edges,
            transform_runs, quarantine, data_contracts, ingest_batches
        TO serving_ro
        """
    )


def downgrade() -> None:
    op.execute(
        """
        REVOKE SELECT ON
            studies, series, instances, artifacts, lineage_edges,
            transform_runs, quarantine, data_contracts, ingest_batches
        FROM serving_ro
        """
    )
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM pipeline_svc")
    op.execute("REVOKE USAGE ON SCHEMA public FROM pipeline_svc, serving_ro")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restricted FROM pipeline_svc")
    op.execute("REVOKE ALL PRIVILEGES ON SCHEMA restricted FROM pipeline_svc")
    op.execute("DROP ROLE IF EXISTS serving_ro")
    op.execute("DROP ROLE IF EXISTS pipeline_svc")

"""Proves the PHI boundary is real: serving_ro has literally no grant on `restricted`,
not a filtered view (refs/DATA-MODEL.md). This is the test that actually proves it,
not just documents it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.fixtures.shared import integration


@integration
def test_serving_role_denied_on_restricted_uid_map(serving_session) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        serving_session.execute(text("SELECT * FROM restricted.uid_map LIMIT 1"))
    assert "permission denied" in str(exc_info.value).lower()


@integration
def test_serving_role_denied_on_restricted_deid_audit(serving_session) -> None:
    with pytest.raises(DBAPIError) as exc_info:
        serving_session.execute(text("SELECT * FROM restricted.deid_audit LIMIT 1"))
    assert "permission denied" in str(exc_info.value).lower()


@integration
def test_serving_role_can_read_public_studies(serving_session) -> None:
    # Sanity check the boundary is scoped correctly -- serving_ro *can* read public.
    result = serving_session.execute(text("SELECT count(*) FROM studies"))
    assert result.scalar_one() >= 0

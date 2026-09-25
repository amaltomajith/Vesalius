"""Shared fixtures. Anything that needs a live Postgres/MinIO is gated behind
`PIPELINE_INTEGRATION_TESTS=1` (set after `docker compose up`) so `pytest tests/unit` runs
standalone, with no Docker dependency, per refs/TESTING.md's own test-layout intent.

A third tier gates anything needing the real `tesseract` OCR binary (S4 redaction, S5
verification, the PHI injection proof): `pytesseract` only wraps the binary and does
nothing without it. It is not installed on the bare host -- only inside the `test` compose
service's image (see docker/api/Dockerfile) -- so `@ocr` tests skip cleanly on the host and
only run for real via `docker compose --profile test run --rm test`.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

INTEGRATION_ENABLED = os.environ.get("PIPELINE_INTEGRATION_TESTS") == "1"

integration = pytest.mark.integration
ocr = pytest.mark.ocr


def require_integration() -> None:
    if not INTEGRATION_ENABLED:
        pytest.skip(
            "requires a live Postgres/MinIO -- run `docker compose up -d postgres minio "
            "minio-init migrate` then set PIPELINE_INTEGRATION_TESTS=1"
        )


def require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip(
            "requires the tesseract binary -- run via `docker compose --profile test run "
            "--rm test`, not on the bare host"
        )


@pytest.fixture
def pipeline_session() -> Iterator[Session]:
    require_integration()
    from pipeline.catalog.session import get_pipeline_session
    from pipeline.common.config import get_settings

    with get_pipeline_session(get_settings()) as session:
        yield session
        session.rollback()


@pytest.fixture
def serving_session() -> Iterator[Session]:
    require_integration()
    from pipeline.catalog.session import get_serving_session
    from pipeline.common.config import get_settings

    with get_serving_session(get_settings()) as session:
        yield session


@pytest.fixture
def bronze_minio_client():  # type: ignore[no-untyped-def]
    require_integration()
    from pipeline.common.config import get_settings
    from pipeline.storage.object_store import bronze_client

    return bronze_client(get_settings())


@pytest.fixture
def silver_minio_client():  # type: ignore[no-untyped-def]
    require_integration()
    from pipeline.common.config import get_settings
    from pipeline.storage.object_store import silver_client

    return silver_client(get_settings())


@pytest.fixture
def gold_minio_client():  # type: ignore[no-untyped-def]
    require_integration()
    from pipeline.common.config import get_settings
    from pipeline.storage.object_store import gold_client

    return gold_client(get_settings())


@pytest.fixture
def ocr_capability() -> None:
    """Request this fixture (alongside pipeline_session/bronze_minio_client) in any test
    that calls into s4_deidentify/s5_verify -- S4 unconditionally runs OCR as part of
    channel 3, so every test touching deidentify_instance/verify_and_promote_instance (or
    the orchestration layer above them) needs the real tesseract binary, not just Postgres/
    MinIO. Mirrors require_integration()'s pattern: skip cleanly, don't error.
    """
    require_tesseract()


@pytest.fixture
def no_ocr_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the REAL S4/S5 code (tag profile, private tags, independent rescan, silver write)
    but stubs only the tesseract call to "no text found" -- for tests about something other
    than OCR (e.g. S6 geometry), so they run on a bare host without the tesseract binary.

    The OCR channel itself is covered by the @ocr tests (real tesseract, inside the `test`
    compose service) and by tests/unit/test_pixel_redaction_logic.py; this fixture is never a
    substitute for those.
    """
    monkeypatch.setattr(
        "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions", lambda image: []
    )
    monkeypatch.setattr(
        "pipeline.stages.s5_verify.pixel_verify.detect_text_regions", lambda image: []
    )

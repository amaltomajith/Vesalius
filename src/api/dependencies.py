from __future__ import annotations

from collections.abc import Iterator

from minio import Minio
from sqlalchemy.orm import Session

from pipeline.catalog.session import get_pipeline_session, get_serving_session
from pipeline.common.config import Settings, get_settings
from pipeline.storage.object_store import bronze_client


def settings_dependency() -> Settings:
    return get_settings()


def pipeline_session_dependency() -> Iterator[Session]:
    with get_pipeline_session(get_settings()) as session:
        yield session


def bronze_client_dependency() -> Minio:
    return bronze_client(get_settings())


def serving_session_dependency() -> Iterator[Session]:
    """The read-only `serving_ro` role -- what every S12 route uses (never the pipeline role)."""
    with get_serving_session(get_settings()) as session:
        yield session

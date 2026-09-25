"""Two session factories, one per database role. S1/S2 only ever use the pipeline
session; the serving session exists so S12 doesn't need this module reworked, and so
tests can prove the serving role has no access to `restricted`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipeline.common.config import Settings, get_settings

_pipeline_engine: Engine | None = None
_serving_engine: Engine | None = None


def _get_pipeline_engine(settings: Settings) -> Engine:
    global _pipeline_engine
    if _pipeline_engine is None:
        _pipeline_engine = create_engine(settings.pipeline_database_url, pool_pre_ping=True)
    return _pipeline_engine


def _get_serving_engine(settings: Settings) -> Engine:
    global _serving_engine
    if _serving_engine is None:
        _serving_engine = create_engine(settings.serving_database_url, pool_pre_ping=True)
    return _serving_engine


@contextmanager
def get_pipeline_session(settings: Settings | None = None) -> Iterator[Session]:
    """The role S1/S2 (and every other stage) use: full access to restricted + public."""
    settings = settings or get_settings()
    engine = _get_pipeline_engine(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_serving_session(settings: Settings | None = None) -> Iterator[Session]:
    """Reserved for S12. Connects as serving_ro -- no grant on `restricted` at all."""
    settings = settings or get_settings()
    engine = _get_serving_engine(settings)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()

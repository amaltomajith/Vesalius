"""Thin wrapper around stages.s1_ingest.service. No ref specifies an API surface for
S1/S2; this exists only as an optional HTTP entrypoint alongside the CLI -- stage logic
itself has no HTTP dependency.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from minio import Minio
from sqlalchemy.orm import Session

from api.dependencies import (
    bronze_client_dependency,
    pipeline_session_dependency,
    settings_dependency,
)
from pipeline.common.config import Settings
from pipeline.common.errors import QuarantineError
from pipeline.stages.s1_ingest.models import IngestBatchResult
from pipeline.stages.s1_ingest.service import ingest

router = APIRouter()


@router.post("/ingest", response_model=IngestBatchResult)
def ingest_archive(
    source: str,
    file: UploadFile,
    session: Annotated[Session, Depends(pipeline_session_dependency)],
    client: Annotated[Minio, Depends(bronze_client_dependency)],
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> IngestBatchResult:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / (file.filename or "upload")
        tmp_path.write_bytes(file.file.read())
        try:
            return ingest(session, client, settings, source=source, path=str(tmp_path))
        except QuarantineError as exc:
            raise HTTPException(
                status_code=422, detail={"reason_code": exc.reason_code, "detail": exc.detail}
            ) from exc

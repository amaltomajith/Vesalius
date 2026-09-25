"""Proves bronze's credential isolation is real -- STAGES.md: "Separate bucket, separate
credentials, never served publicly." A non-bronze credential must not be able to read or
write the bronze bucket.
"""

from __future__ import annotations

import io

import pytest
from minio.error import S3Error

from pipeline.common.config import get_settings
from pipeline.storage.object_store import silver_client
from tests.fixtures.shared import integration


@integration
def test_silver_credentials_cannot_read_bronze_bucket() -> None:
    settings = get_settings()
    client = silver_client(settings)
    with pytest.raises(S3Error):
        client.stat_object(settings.minio_bronze_bucket, "00/nonexistent.dcm")


@integration
def test_silver_credentials_cannot_write_bronze_bucket() -> None:
    settings = get_settings()
    client = silver_client(settings)
    with pytest.raises(S3Error):
        client.put_object(
            settings.minio_bronze_bucket,
            "00/should-not-write.dcm",
            io.BytesIO(b"data"),
            length=4,
        )

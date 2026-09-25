"""Bronze write-once semantics, tested against a fake Minio client -- no live MinIO
needed (STAGES.md: "Bronze is write-once. Never modify a file in bronze.").
"""

from __future__ import annotations

from minio.error import S3Error

from pipeline.common.config import Settings
from pipeline.storage.bronze import bronze_exists, bronze_key, write_once


class FakeMinioClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[tuple[str, str]] = []

    def stat_object(self, bucket: str, key: str) -> None:
        if (bucket, key) not in self.objects:
            raise S3Error(
                response=None,
                code="NoSuchKey",
                message="not found",
                resource=key,
                request_id="r",
                host_id="h",
            )

    def put_object(self, bucket: str, key: str, data, length: int, content_type: str) -> None:
        self.put_calls.append((bucket, key))
        self.objects[(bucket, key)] = data.read()


def test_bronze_key_format() -> None:
    content_hash = "ab" * 32
    assert bronze_key(content_hash) == f"ab/{content_hash}.dcm"


def test_write_once_stores_new_content() -> None:
    client = FakeMinioClient()
    settings = Settings()
    key = write_once(client, settings, "a" * 64, b"dicom bytes")
    assert key == bronze_key("a" * 64)
    assert client.objects[(settings.minio_bronze_bucket, key)] == b"dicom bytes"
    assert len(client.put_calls) == 1


def test_write_once_is_idempotent_second_write_is_noop() -> None:
    client = FakeMinioClient()
    settings = Settings()
    content_hash = "b" * 64
    write_once(client, settings, content_hash, b"first write")
    write_once(client, settings, content_hash, b"first write")
    assert len(client.put_calls) == 1


def test_bronze_exists_reflects_prior_write() -> None:
    client = FakeMinioClient()
    settings = Settings()
    content_hash = "c" * 64
    assert not bronze_exists(client, settings, content_hash)
    write_once(client, settings, content_hash, b"payload")
    assert bronze_exists(client, settings, content_hash)

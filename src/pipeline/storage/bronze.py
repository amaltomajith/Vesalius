"""Bronze zone: content-addressed, write-once. STAGES.md: "Bronze is write-once. Never
modify a file in bronze." Since the key is `sha256(bytes)`, any second write of the same
key is guaranteed byte-identical -- write-once is structurally satisfied by content
addressing, enforced here by simply never overwriting an existing key.
"""

from __future__ import annotations

from minio import Minio

from pipeline.common.config import Settings
from pipeline.storage.object_store import object_exists, put_bytes


def bronze_key(content_hash: str) -> str:
    return f"{content_hash[:2]}/{content_hash}.dcm"


def bronze_exists(client: Minio, settings: Settings, content_hash: str) -> bool:
    return object_exists(client, settings.minio_bronze_bucket, bronze_key(content_hash))


def write_once(client: Minio, settings: Settings, content_hash: str, data: bytes) -> str:
    """Writes `data` to bronze under its content-addressed key, unless it is already
    there (write-once, idempotent under crash-recovery). Returns the bronze key.
    """
    key = bronze_key(content_hash)
    if not object_exists(client, settings.minio_bronze_bucket, key):
        put_bytes(client, settings.minio_bronze_bucket, key, data, "application/dicom")
    return key

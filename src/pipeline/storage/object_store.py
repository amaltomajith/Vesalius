"""MinIO client wrapper, one client per zone bucket. Bronze's isolation (separate bucket,
separate credentials) is explicit in STAGES.md; silver/gold/quarantine follow the same
pattern for a uniform credential boundary (gold needs its own presigned-URL credentials
at S12 regardless).
"""

from __future__ import annotations

import io

from minio import Minio

from pipeline.common.config import Settings


def _client_for(settings: Settings, access_key: str, secret_key: str) -> Minio:
    # region is pinned rather than left to auto-discovery: the minio-py client's first
    # request on an unscoped client issues a GetBucketLocation call to discover it, which
    # our per-bucket scoped policies (GetObject/PutObject/ListBucket only) don't grant --
    # standalone MinIO's implicit region is always "us-east-1" regardless of MINIO_REGION
    # being unset, so this is safe rather than a guess.
    return Minio(
        settings.minio_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=settings.minio_secure,
        region="us-east-1",
    )


def bronze_client(settings: Settings) -> Minio:
    return _client_for(settings, settings.minio_bronze_access_key, settings.minio_bronze_secret_key)


def silver_client(settings: Settings) -> Minio:
    return _client_for(settings, settings.minio_silver_access_key, settings.minio_silver_secret_key)


def gold_client(settings: Settings) -> Minio:
    return _client_for(settings, settings.minio_gold_access_key, settings.minio_gold_secret_key)


def gold_presign_client(settings: Settings) -> Minio:
    """A gold-credentialed client bound to the PUBLIC endpoint, for signing URLs only (region is
    pinned, so signing makes no network call). Never used to read or write objects."""
    return Minio(
        settings.minio_public_endpoint or settings.minio_endpoint,
        access_key=settings.minio_gold_access_key,
        secret_key=settings.minio_gold_secret_key,
        secure=settings.minio_secure,
        region="us-east-1",
    )


def quarantine_client(settings: Settings) -> Minio:
    return _client_for(
        settings, settings.minio_quarantine_access_key, settings.minio_quarantine_secret_key
    )


def object_exists(client: Minio, bucket: str, key: str) -> bool:
    from minio.error import S3Error

    try:
        client.stat_object(bucket, key)
        return True
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return False
        raise


def put_bytes(client: Minio, bucket: str, key: str, data: bytes, content_type: str) -> None:
    client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)


def get_bytes(client: Minio, bucket: str, key: str) -> bytes:
    """Shared read helper. S2/S3/S4/S5 each independently define a private
    `_read_bronze_bytes`-style duplicate of this exact pattern -- left untouched rather than
    retrofitted (not worth the risk of touching already-verified code for a DRY cleanup).
    S6 is the first caller of this shared version.
    """
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()

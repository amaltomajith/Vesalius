"""Presigned download URLs. The bucket is NOT a parameter: `presign_gold_url` can only ever sign
`settings.minio_gold_bucket`, so no caller can ask it for a silver or bronze object. The object key
is whatever the catalog row says -- callers pass it only after looking up a `zone == 'gold'` row.
"""

from __future__ import annotations

from datetime import timedelta

from pipeline.common.config import Settings
from pipeline.storage.object_store import gold_presign_client


def presign_gold_url(settings: Settings, object_key: str) -> tuple[str, int]:
    """(url, expires_in_seconds)."""
    ttl = settings.presign_expiry_seconds
    url = gold_presign_client(settings).presigned_get_object(
        settings.minio_gold_bucket, object_key, expires=timedelta(seconds=ttl)
    )
    return url, ttl

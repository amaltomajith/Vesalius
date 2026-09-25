"""Content hashing and the deterministic id shared between S1 and S2.

S1 cannot insert into `instances` (series_id is NOT NULL and only S2 knows the series),
so S1's transform_runs use `uuid5(NAMESPACE_BRONZE, content_hash)` as a stand-in subject
id. S2 later creates the real `instances` row with the identical id, so the two stages'
audit trails line up without a lookup table.
"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid5

# Fixed, arbitrary namespace UUID for this project. Never change this value -- doing so
# would silently break the S1/S2 id correspondence for every previously ingested instance.
NAMESPACE_BRONZE = UUID("d9f6a1e2-8b3c-4f5a-9d7e-2c6b1a0f4e3d")

_CHUNK_SIZE = 1024 * 1024


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def instance_id_for_content_hash(content_hash: str) -> UUID:
    return uuid5(NAMESPACE_BRONZE, content_hash)

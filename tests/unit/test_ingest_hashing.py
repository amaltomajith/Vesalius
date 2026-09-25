from __future__ import annotations

import hashlib
from pathlib import Path

from pipeline.common.hashing import (
    NAMESPACE_BRONZE,
    instance_id_for_content_hash,
    sha256_of_bytes,
    sha256_of_file,
)


def test_sha256_of_bytes_matches_stdlib() -> None:
    data = b"some dicom-looking bytes"
    assert sha256_of_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_of_file_matches_bytes_hash(tmp_path: Path) -> None:
    data = b"\x00" * 128 + b"DICM" + b"rest of the file"
    file_path = tmp_path / "sample.dcm"
    file_path.write_bytes(data)

    assert sha256_of_file(str(file_path)) == sha256_of_bytes(data)


def test_instance_id_is_deterministic_for_same_content_hash() -> None:
    content_hash = sha256_of_bytes(b"identical content")
    id_a = instance_id_for_content_hash(content_hash)
    id_b = instance_id_for_content_hash(content_hash)
    assert id_a == id_b


def test_instance_id_differs_for_different_content_hash() -> None:
    id_a = instance_id_for_content_hash(sha256_of_bytes(b"content A"))
    id_b = instance_id_for_content_hash(sha256_of_bytes(b"content B"))
    assert id_a != id_b


def test_instance_id_uses_fixed_namespace() -> None:
    from uuid import uuid5

    content_hash = sha256_of_bytes(b"content C")
    assert instance_id_for_content_hash(content_hash) == uuid5(NAMESPACE_BRONZE, content_hash)

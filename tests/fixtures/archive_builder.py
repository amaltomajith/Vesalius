"""zip/tar builders for S1 archive-handling tests."""

from __future__ import annotations

import tarfile
import zipfile
from io import BytesIO
from pathlib import Path


def build_zip(tmp_path: Path, name: str, entries: dict[str, bytes]) -> Path:
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w") as zf:
        for filename, data in entries.items():
            zf.writestr(filename, data)
    return archive_path


def build_tar(tmp_path: Path, name: str, entries: dict[str, bytes], mode: str = "w") -> Path:
    archive_path = tmp_path / name
    with tarfile.open(archive_path, mode) as tf:
        for filename, data in entries.items():
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))
    return archive_path


def build_corrupt_zip(tmp_path: Path, name: str) -> Path:
    archive_path = tmp_path / name
    archive_path.write_bytes(b"PK\x03\x04not a real zip file")
    return archive_path

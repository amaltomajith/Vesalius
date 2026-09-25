"""Archive and directory enumeration for S1, plus the DICM magic check.

Supports zip, tar, tar.gz, tar.bz2 (via `tarfile`'s transparent compression modes --
refs say "zip/tar"; the compressed tar variants are effectively free to support with the
same library) and plain directories.
"""

from __future__ import annotations

import os
import tarfile
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from pipeline.common.errors import UnreadableArchiveError

DICM_MAGIC = b"DICM"
DICM_OFFSET = 128


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    data: bytes


def is_dicom(data: bytes) -> bool:
    """DICM magic check at byte offset 128 (STAGES.md S1)."""
    return len(data) >= DICM_OFFSET + len(DICM_MAGIC) and (
        data[DICM_OFFSET : DICM_OFFSET + len(DICM_MAGIC)] == DICM_MAGIC
    )


def _iter_zip(path: str) -> Iterator[ArchiveEntry]:
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UnreadableArchiveError({"path": path, "error": str(exc)}) from exc
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            with zf.open(info) as fh:
                yield ArchiveEntry(name=info.filename, data=fh.read())


def _iter_tar(path: str) -> Iterator[ArchiveEntry]:
    try:
        tf = tarfile.open(path, mode="r:*")  # noqa: SIM115 - must catch open() errors first
    except (tarfile.ReadError, OSError, tarfile.CompressionError) as exc:
        raise UnreadableArchiveError({"path": path, "error": str(exc)}) from exc
    with tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            yield ArchiveEntry(name=member.name, data=extracted.read())


def _iter_directory(path: str) -> Iterator[ArchiveEntry]:
    for root, _dirs, files in os.walk(path):
        for filename in files:
            full_path = os.path.join(root, filename)
            rel_name = os.path.relpath(full_path, path)
            try:
                with open(full_path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise UnreadableArchiveError({"path": full_path, "error": str(exc)}) from exc
            yield ArchiveEntry(name=rel_name, data=data)


def iter_source(path: str) -> Iterator[ArchiveEntry]:
    """Dispatches to the right enumerator: directory, zip, or tar (any compression)."""
    if os.path.isdir(path):
        yield from _iter_directory(path)
        return
    if not os.path.exists(path):
        raise UnreadableArchiveError({"path": path, "error": "path does not exist"})
    if zipfile.is_zipfile(path):
        yield from _iter_zip(path)
        return
    try:
        is_tar = tarfile.is_tarfile(path)
    except OSError as exc:
        raise UnreadableArchiveError({"path": path, "error": str(exc)}) from exc
    if is_tar:
        yield from _iter_tar(path)
        return
    raise UnreadableArchiveError({"path": path, "error": "unrecognized archive format"})

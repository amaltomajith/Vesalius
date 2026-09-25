from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.common.errors import UnreadableArchiveError
from pipeline.stages.s1_ingest.archive import iter_source
from tests.fixtures.archive_builder import build_corrupt_zip, build_tar, build_zip
from tests.fixtures.dicom_factory import build_dicom_bytes


def test_iter_zip_with_mixed_dicom_and_non_dicom(tmp_path: Path) -> None:
    dicom_bytes = build_dicom_bytes()
    archive = build_zip(
        tmp_path,
        "mixed.zip",
        {"scan1.dcm": dicom_bytes, "README.txt": b"not dicom"},
    )
    entries = list(iter_source(str(archive)))
    names = {e.name for e in entries}
    assert names == {"scan1.dcm", "README.txt"}


def test_iter_tar_plain(tmp_path: Path) -> None:
    dicom_bytes = build_dicom_bytes()
    archive = build_tar(tmp_path, "scan.tar", {"scan1.dcm": dicom_bytes})
    entries = list(iter_source(str(archive)))
    assert len(entries) == 1
    assert entries[0].data == dicom_bytes


def test_iter_tar_gz(tmp_path: Path) -> None:
    dicom_bytes = build_dicom_bytes()
    archive = build_tar(tmp_path, "scan.tar.gz", {"scan1.dcm": dicom_bytes}, mode="w:gz")
    entries = list(iter_source(str(archive)))
    assert len(entries) == 1


def test_iter_tar_bz2(tmp_path: Path) -> None:
    dicom_bytes = build_dicom_bytes()
    archive = build_tar(tmp_path, "scan.tar.bz2", {"scan1.dcm": dicom_bytes}, mode="w:bz2")
    entries = list(iter_source(str(archive)))
    assert len(entries) == 1


def test_iter_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    source_dir.mkdir()
    (source_dir / "a.dcm").write_bytes(build_dicom_bytes())
    (source_dir / "b.dcm").write_bytes(build_dicom_bytes())
    entries = list(iter_source(str(source_dir)))
    assert len(entries) == 2


def test_corrupted_zip_raises_unreadable_archive(tmp_path: Path) -> None:
    archive = build_corrupt_zip(tmp_path, "bad.zip")
    with pytest.raises(UnreadableArchiveError) as exc_info:
        list(iter_source(str(archive)))
    assert exc_info.value.reason_code == "S1_UNREADABLE_ARCHIVE"


def test_missing_path_raises_unreadable_archive() -> None:
    with pytest.raises(UnreadableArchiveError):
        list(iter_source("F:/definitely/does/not/exist.zip"))


def test_all_non_dicom_archive_yields_no_dicom_entries(tmp_path: Path) -> None:
    archive = build_zip(tmp_path, "junk.zip", {"README.txt": b"just text", "notes.md": b"# hi"})
    entries = list(iter_source(str(archive)))
    assert entries  # entries exist...
    from pipeline.stages.s1_ingest.archive import is_dicom

    assert not any(is_dicom(e.data) for e in entries)  # ...but none are DICOM

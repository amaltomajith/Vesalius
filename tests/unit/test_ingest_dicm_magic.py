from __future__ import annotations

from pipeline.stages.s1_ingest.archive import DICM_OFFSET, is_dicom
from tests.fixtures.dicom_factory import build_dicom_bytes


def test_valid_dicom_magic_accepted() -> None:
    data = build_dicom_bytes()
    assert data[DICM_OFFSET : DICM_OFFSET + 4] == b"DICM"
    assert is_dicom(data)


def test_missing_magic_rejected() -> None:
    data = b"\x00" * 200
    assert not is_dicom(data)


def test_wrong_magic_bytes_rejected() -> None:
    data = bytearray(build_dicom_bytes())
    data[DICM_OFFSET : DICM_OFFSET + 4] = b"NOPE"
    assert not is_dicom(bytes(data))


def test_too_short_to_contain_magic_rejected() -> None:
    assert not is_dicom(b"short")


def test_plain_text_file_rejected() -> None:
    assert not is_dicom(b"This is just a README, not DICOM.\n" * 10)

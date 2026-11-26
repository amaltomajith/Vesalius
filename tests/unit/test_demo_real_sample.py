"""The real-sample fetcher's selection and safety checks, with no network."""

from __future__ import annotations

import hashlib
import io
import zipfile
from unittest import mock

import pydicom
import pytest

from pipeline.demo import real_sample
from pipeline.demo.dicom_series import build_demo_series
from pipeline.demo.real_sample import RealSampleError, build_zip, select_axial_series


def _coronal_copy(blob: bytes) -> bytes:
    ds = pydicom.dcmread(io.BytesIO(blob))
    ds.ImageOrientationPatient = [1, 0, 0, 0, 0, -1]
    out = io.BytesIO()
    ds.save_as(out)
    return out.getvalue()


def test_the_axial_series_is_chosen_even_when_a_bigger_series_is_not_axial() -> None:
    axial = build_demo_series("chest", plant_identifiers=False).files  # 34 axial images
    bigger_but_coronal = {
        f"c{n}": _coronal_copy(b)
        for n, b in build_demo_series("head", plant_identifiers=False).files.items()
    }  # 106 images
    junk = {"DICOMDIR": b"not an image", "preview.jpg": b"\xff\xd8\xff", "empty": b""}
    choice = select_axial_series({**axial, **bigger_but_coronal, **junk})
    assert len(choice.files) == len(axial)


def test_a_series_with_too_few_images_is_refused_with_a_plain_message() -> None:
    few = dict(list(build_demo_series("chest", plant_identifiers=False).files.items())[:5])
    with pytest.raises(RealSampleError, match="only 5 images"):
        select_axial_series(few)


def test_nothing_usable_is_refused() -> None:
    with pytest.raises(RealSampleError, match="No axial CT series"):
        select_axial_series({"a": b"x", "b": b"y"})


def test_the_output_zip_holds_only_the_chosen_images() -> None:
    files = build_demo_series("chest", plant_identifiers=False).files
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        for name, blob in files.items():
            zf.writestr(f"DICOM/{name}", blob)
        zf.writestr("DICOMDIR", b"index")
        zf.writestr("preview.png", b"pic")
    data, choice = build_zip(archive.getvalue())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert len(zf.namelist()) == len(choice.files) == len(files)
        assert all(n.endswith(".dcm") for n in zf.namelist())


def test_a_download_that_is_not_the_expected_file_is_never_used() -> None:
    fake = mock.MagicMock()
    fake.__enter__.return_value.read.return_value = b"something else entirely"
    with (
        mock.patch("urllib.request.urlopen", return_value=fake),
        pytest.raises(RealSampleError, match="checksum differs"),
    ):
        real_sample.download()


def test_a_network_failure_says_what_to_do() -> None:
    with (
        mock.patch("urllib.request.urlopen", side_effect=OSError("offline")),
        pytest.raises(RealSampleError, match="Check the internet connection"),
    ):
        real_sample.download()


def test_the_pinned_checksum_is_a_sha256() -> None:
    assert len(real_sample.SOURCE_SHA256) == 64
    assert real_sample.SOURCE_SHA256.lower() == real_sample.SOURCE_SHA256
    assert hashlib.sha256(b"").hexdigest() != real_sample.SOURCE_SHA256

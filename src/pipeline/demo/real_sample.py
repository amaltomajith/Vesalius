"""Fetches a real, public head CT and turns it into a zip the upload page accepts.

    python -m pipeline.demo.real_sample OUTPUT.zip

Why this exists: real public downloads are usually a whole study folder (several series, a
DICOMDIR index, preview pictures). The pipeline takes ONE series, so a raw download is refused.
This downloads the file, checks it is byte-for-byte the file this was written against, and keeps
only the main axial series.

Source: Medimodel's free sample "Human Skull 2" (a CT; anonymised by the provider, offered "for
education and research"). It is NOT stored in this repository: it is fetched from the provider
each time, and its terms are the provider's. Nothing else is downloaded.
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pydicom

SOURCE_PAGE = "https://medimodel.com/sample-dicom-files/human_skull_2_dicom_file/"
SOURCE_URL = "https://medimodel.com/wp-content/uploads/2021/03/2_skull_ct.zip"
SOURCE_SHA256 = "3e920cb087345a6b6a22989941da8f90cc69eecec36b337ae5f40a58edd20e2a"
MIN_SLICES = 30
_AXIAL_MIN_NORMAL_Z = 0.99  # |z component of the slice normal|: 1.0 is exactly axial


class RealSampleError(RuntimeError):
    """The download or the series selection failed; the message says what to do."""


@dataclass(frozen=True)
class Choice:
    series_uid: str
    files: list[bytes]
    slice_thickness_mm: float | None


def _normal_z(orientation: list[float]) -> float:
    row, col = np.array(orientation[:3]), np.array(orientation[3:6])
    return float(abs(np.cross(row, col)[2]))


def select_axial_series(files: dict[str, bytes]) -> Choice:
    """The largest axial CT series among `files` (name -> bytes). Anything that is not a readable
    DICOM image (a DICOMDIR index, preview pictures) is ignored. Raises RealSampleError if no
    series has at least MIN_SLICES axial images."""
    groups: dict[str, list[tuple[bytes, float | None]]] = {}
    for blob in files.values():
        try:
            ds = pydicom.dcmread(io.BytesIO(blob), stop_before_pixels=True)
        except Exception:  # noqa: BLE001 -- pictures and index files are simply not images
            continue
        if ds.get("Modality") != "CT" or "SeriesInstanceUID" not in ds:
            continue
        orient = ds.get("ImageOrientationPatient")
        if orient is None or _normal_z([float(x) for x in orient]) < _AXIAL_MIN_NORMAL_Z:
            continue
        thickness = ds.get("SliceThickness")
        groups.setdefault(str(ds.SeriesInstanceUID), []).append(
            (blob, None if thickness is None else float(thickness))
        )
    if not groups:
        raise RealSampleError("No axial CT series was found in the download.")
    uid, members = max(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
    if len(members) < MIN_SLICES:
        raise RealSampleError(f"The largest axial series has only {len(members)} images.")
    return Choice(uid, [m[0] for m in members], members[0][1])


def download(url: str = SOURCE_URL, expected_sha256: str = SOURCE_SHA256) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 -- fixed https URL
            data: bytes = response.read()
    except OSError as exc:
        raise RealSampleError(
            f"Could not download the sample ({type(exc).__name__}). Check the internet "
            f"connection, or download it by hand from {SOURCE_PAGE}"
        ) from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise RealSampleError(
            "The downloaded file is not the one this was written for (its checksum differs), "
            "so it was not used. The provider may have changed it."
        )
    return data


def build_zip(archive: bytes) -> tuple[bytes, Choice]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    choice = select_axial_series(files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for i, blob in enumerate(choice.files):
            out.writestr(f"{i:04d}.dcm", blob)
    return buf.getvalue(), choice


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m pipeline.demo.real_sample OUTPUT.zip", file=sys.stderr)
        return 2
    try:
        data, choice = build_zip(download())
    except RealSampleError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    Path(argv[1]).write_bytes(data)
    print(
        f"Saved {len(choice.files)} axial CT images "
        f"({(choice.slice_thickness_mm or 0):g} mm slices) to {argv[1]}"
    )
    print(f"Source and terms: {SOURCE_PAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

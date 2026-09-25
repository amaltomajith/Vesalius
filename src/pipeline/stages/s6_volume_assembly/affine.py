"""LPS affine construction and LPS->RAS conversion. DICOM.md Part 2, transcribed
near-verbatim -- this is the authoritative reference, not reinterpreted.

Column order is (x=column-index, y=row-index, z=slice-index) -- matches sitk.Image's own
Direction/Spacing/Origin convention exactly (see resampling.py/service.py), NOT the numpy
array's (z,y,x) storage order. Do not permute these columns to "match" the array -- the
apparent mismatch is resolved automatically by sitk.GetImageFromArray/GetArrayFromImage's
documented axis reversal, not by a manual transpose here.

LPS vs RAS -- verified empirically against the raw NIfTI header, not assumed:
SimpleITK/ITK's physical space is LPS (same as DICOM), and ITK's NIfTI writer converts to
the file's RAS convention ITSELF on write (and back on read). So the sitk.Image must be
handed DICOM-native LPS geometry; pre-flipping it to RAS would make ITK flip a second time
and leave LPS-valued geometry in a header labelled RAS -- a silent x/y mirror. Crucially, a
sitk-write -> sitk-read round trip CANNOT detect that (the two flips cancel on read), which
is why `read_nifti_sform` parses the header bytes directly, independent of ITK.
"""

from __future__ import annotations

import gzip
import struct
from collections.abc import Sequence

import numpy as np

# DICOM.md Part 2: "LPS -> RAS is a sign flip on the first two axes."
LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])


def affine_lps(
    row_cosine: Sequence[float],
    col_cosine: Sequence[float],
    normal: Sequence[float],
    dx: float,
    dy: float,
    spacing_z: float,
    origin: Sequence[float],
) -> np.ndarray:
    """DICOM.md Part 2's affine_lps(), transcribed near-verbatim.

    dx/dy follow DICOM.md's own naming: PixelSpacing is [row, column] = [y, x], so `dx` is
    column spacing and `dy` is row spacing -- matching the column order below exactly.
    """
    a = np.eye(4)
    a[:3, 0] = np.array(row_cosine, dtype=float) * dx
    a[:3, 1] = np.array(col_cosine, dtype=float) * dy
    a[:3, 2] = np.array(normal, dtype=float) * spacing_z
    a[:3, 3] = np.array(origin, dtype=float)
    return a


def affine_ras(affine_lps_matrix: np.ndarray) -> np.ndarray:
    return LPS_TO_RAS @ affine_lps_matrix


def parse_nifti_sform(header: bytes) -> np.ndarray:
    """The sform (srow_x/y/z) of the first 348 bytes of a little-endian NIfTI-1 header."""
    (sizeof_hdr,) = struct.unpack("<i", header[0:4])
    if sizeof_hdr != 348:
        raise ValueError(f"not a little-endian NIfTI-1 header (sizeof_hdr={sizeof_hdr})")
    (sform_code,) = struct.unpack("<h", header[254:256])
    if sform_code <= 0:
        raise ValueError("NIfTI header carries no sform (sform_code == 0)")
    matrix = np.eye(4)
    for row, offset in enumerate((280, 296, 312)):
        matrix[row, :] = struct.unpack("<4f", header[offset : offset + 16])
    return matrix


def read_nifti_sform(path: str) -> np.ndarray:
    """Parses the sform (srow_x/y/z) straight out of a NIfTI-1 .nii.gz header -- the affine a
    RAS-consuming tool (nibabel, FSL, ...) will actually use. Deliberately independent of
    ITK's own reader, which round-trips LPS<->RAS symmetrically and so cannot reveal a header
    that is wrong (see module docstring).
    """
    with gzip.open(path, "rb") as fh:
        header = fh.read(348)
    return parse_nifti_sform(header)

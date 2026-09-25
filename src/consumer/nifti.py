"""NIfTI-1 read straight from the bytes -- header fields and the float32 payload -- with no ITK.

ITK converts to/from its own LPS physical space symmetrically, so a read through it cannot reveal
a wrong header (the S6 lesson). Reading the raw sform is what a RAS-native tool (nibabel, FSL)
would actually use, so it is what the consumer validates.
"""

from __future__ import annotations

import gzip
import struct
from dataclasses import dataclass

import numpy as np

from consumer.contract import ContractViolation

HEADER_BYTES = 348
FLOAT32 = 16
UNITS_MM = 2


@dataclass(frozen=True)
class Header:
    dim: tuple[int, ...]  # dim[1:1+ndim], x-fastest (NIfTI order)
    datatype: int
    pixdim: tuple[float, float, float]
    xyzt_units: int
    sform_code: int
    sform: np.ndarray  # 4x4
    vox_offset: int


def read_header(raw: bytes) -> Header:
    if len(raw) < HEADER_BYTES:
        raise ContractViolation("not a readable NIfTI-1 volume (truncated header)")
    (sizeof_hdr,) = struct.unpack("<i", raw[0:4])
    if sizeof_hdr != HEADER_BYTES:
        raise ContractViolation("not a readable little-endian NIfTI-1 volume")
    dims = struct.unpack("<8h", raw[40:56])
    ndim = dims[0]
    (datatype,) = struct.unpack("<h", raw[70:72])
    pixdim = struct.unpack("<8f", raw[76:108])
    (vox_offset,) = struct.unpack("<f", raw[108:112])
    xyzt_units = raw[123]
    (sform_code,) = struct.unpack("<h", raw[254:256])
    sform = np.eye(4)
    for row, off in enumerate((280, 296, 312)):
        sform[row, :] = struct.unpack("<4f", raw[off : off + 16])
    return Header(
        dim=tuple(int(d) for d in dims[1 : 1 + max(ndim, 0)]),
        datatype=datatype,
        pixdim=(pixdim[1], pixdim[2], pixdim[3]),
        xyzt_units=xyzt_units,
        sform_code=sform_code,
        sform=sform,
        vox_offset=int(vox_offset),
    )


def gunzip(nii_gz: bytes) -> bytes:
    try:
        return gzip.decompress(nii_gz)
    except (OSError, EOFError) as exc:
        raise ContractViolation("not a readable gzip-compressed NIfTI-1 volume") from exc


def read_float32(raw: bytes, header: Header) -> np.ndarray:
    """(z, y, x) float32. NIfTI stores x fastest, so C-order reshape of (nz, ny, nx)."""
    nx, ny, nz = header.dim
    count = nx * ny * nz
    payload = raw[header.vox_offset : header.vox_offset + count * 4]
    if len(payload) != count * 4:
        raise ContractViolation("volume payload is shorter than its header declares")
    return np.frombuffer(payload, dtype="<f4").reshape(nz, ny, nx).copy()

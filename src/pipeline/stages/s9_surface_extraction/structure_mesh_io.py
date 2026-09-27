"""S9 structures mode -- the npz format for per-structure meshes (read by S10 and S11).

A plain reader/writer for the format S9 happens to write, like `mesh_io` for the threshold path:
downstream stages may import this, never S9's meshing algorithm or service.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class StructureMesh:
    node: str
    vertices_ras: np.ndarray  # (n, 3) float64, RAS mm
    faces: np.ndarray  # (m, 3) int64


def pack(meshes: list[StructureMesh], normals: dict[str, np.ndarray] | None = None) -> bytes:
    """Byte-deterministic npz: `v::<node>` float64 RAS mm, `f::<node>` int64, optional
    `n::<node>` normals. np.savez stamps entries with the current time, so the zip is written
    here with a fixed epoch (CLAUDE.md "Determinism")."""
    import io
    import zipfile

    entries: list[tuple[str, np.ndarray]] = []
    for m in sorted(meshes, key=lambda m: m.node):
        entries.append((f"v::{m.node}", np.ascontiguousarray(m.vertices_ras, dtype=np.float64)))
        entries.append((f"f::{m.node}", np.ascontiguousarray(m.faces, dtype=np.int64)))
        if normals is not None:
            entries.append(
                (f"n::{m.node}", np.ascontiguousarray(normals[m.node], dtype=np.float64))
            )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, arr in entries:
            one = io.BytesIO()
            np.lib.format.write_array(one, arr, allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, one.getvalue())
    return buf.getvalue()


def unpack_normals(data: bytes) -> dict[str, np.ndarray]:
    import io

    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        return {k[3:]: z[k] for k in z.files if k.startswith("n::")}


def unpack(data: bytes) -> list[StructureMesh]:
    import io

    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        nodes = sorted(k[3:] for k in z.files if k.startswith("v::"))
        return [StructureMesh(node=n, vertices_ras=z[f"v::{n}"], faces=z[f"f::{n}"]) for n in nodes]

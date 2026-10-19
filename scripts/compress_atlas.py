"""Compress the full-body atlas for the static site, without changing anything the viewer keys on.

GitHub Pages serves GLBs uncompressed, and the atlas's three default systems are ~77 MB: minutes
to download. Draco (KHR_draco_mesh_compression) brings them to ~15 MB. It only re-encodes each
primitive's geometry, so the scene graph stays as it was; that is checked here, file by file,
because the viewer keys entirely on GLB node names (CLAUDE.md, "node names surviving export"):
same node names in the same order, same hierarchy, same mesh on every node, same meshes and
materials. (Meshopt was rejected: its quantization inserts unnamed wrapper nodes.)

The repository's atlas (assets/atlas) is left as it is: its licence tests read those bytes, and
the API serves them. Only the site's copy is compressed, and its atlas.json gets the compressed
files' sizes and hashes.

    python scripts/compress_atlas.py assets/atlas site/app/atlas
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

GLTF_TRANSFORM = ["npx", "--yes", "@gltf-transform/cli@4.2.1"]
TOLERANCE = 1e-4  # node translations the encoder rounds to zero are ~2e-6 here (about 0.01 mm)

Compress = Callable[[Path, Path], None]


def gltf_json(glb: bytes) -> dict[str, Any]:
    if glb[:4] != b"glTF":
        raise ValueError("not a GLB")
    length, kind = struct.unpack("<II", glb[12:20])
    if kind != 0x4E4F534A:  # "JSON"
        raise ValueError("the GLB's first chunk is not JSON")
    return dict(json.loads(glb[20 : 20 + length]))


def structure_differences(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Everything the viewer could notice, compared; [] when the scene graph is unchanged."""
    problems: list[str] = []
    a, b = before.get("nodes", []), after.get("nodes", [])
    if [n.get("name") for n in a] != [n.get("name") for n in b]:
        problems.append(f"node names differ ({len(a)} -> {len(b)} nodes)")
        return problems
    for x, y in zip(a, b, strict=True):
        name = x.get("name")
        if x.get("children", []) != y.get("children", []):
            problems.append(f"{name}: children differ")
        if x.get("mesh") != y.get("mesh"):
            problems.append(f"{name}: mesh differs")
        for key, default in (("translation", 0.0), ("rotation", None), ("scale", 1.0)):
            p, q = x.get(key), y.get(key)
            if p is None and q is None:
                continue
            if default is None:
                p, q = p or [0.0, 0.0, 0.0, 1.0], q or [0.0, 0.0, 0.0, 1.0]
            else:
                p, q = p or [default] * 3, q or [default] * 3
            if any(abs(u - v) > TOLERANCE for u, v in zip(p, q, strict=True)):
                problems.append(f"{name}: {key} differs")
    if [m.get("name") for m in before.get("meshes", [])] != [
        m.get("name") for m in after.get("meshes", [])
    ]:
        problems.append("mesh names differ")
    if before.get("materials") != after.get("materials"):
        problems.append("materials differ")
    if before.get("scenes") != after.get("scenes"):
        problems.append("scenes differ")
    return problems


def draco(src: Path, out: Path) -> None:
    subprocess.run([*GLTF_TRANSFORM, "draco", str(src), str(out)], check=True, capture_output=True)  # noqa: S603


def compress_atlas(src: Path, out: Path, compress: Compress = draco) -> list[tuple[str, int, int]]:
    """Copy `src` to `out` with every group GLB compressed and checked; returns (file, before,
    after) sizes. Raises ValueError, writing nothing usable, if any scene graph changed."""
    manifest = json.loads((src / "atlas.json").read_text(encoding="utf-8"))
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=shutil.ignore_patterns("*.glb"))
    sizes = []
    for group in manifest["groups"]:
        before, after = src / group["file"], out / group["file"]
        compress(before, after)
        problems = structure_differences(
            gltf_json(before.read_bytes()), gltf_json(after.read_bytes())
        )
        if problems:
            shutil.rmtree(out)
            raise ValueError(
                f"{group['file']}: compression changed the scene graph: {problems[:5]}"
            )
        data = after.read_bytes()
        group["bytes"], group["sha256"] = len(data), hashlib.sha256(data).hexdigest()
        sizes.append((group["file"], before.stat().st_size, len(data)))
    (out / "atlas.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=1), encoding="utf-8"
    )
    return sizes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        sizes = compress_atlas(Path(sys.argv[1]), Path(sys.argv[2]))
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    for name, before, after in sizes:
        print(f"{name}: {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

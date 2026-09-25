"""Structural proof that the raw S6 volume and the S7/S8/S9/S10 internals are reachable only
where they should be. Downstream stages get volume bytes ONLY via
`s7_deface.gate.open_defacing_gate`, and reach S8/S9/S10's own artifacts only via their
`Artifact` rows + a generic reader module (`nifti_io`/`mesh_io` -- never a producer's
`service`/algorithm module directly) -- this scans the source tree so a shortcut cannot slip in
unnoticed. Limits: it is a textual/AST scan of this repository -- it stops accidents, not a
determined reader of the silver key.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "pipeline"
API = Path(__file__).parents[2] / "src" / "api"

RAW_NAMES = ("volume.nii.gz", "volume_nifti", "volume_defaced")
# where the raw names legitimately appear. s11_export is included because it WRITES the gold
# volume.nii.gz copy (kind="volume_nifti") -- its bytes still come only from
# `GatedVolume.volume_bytes` (the gate), never a direct silver read; this is the naming of S11's
# own output, not a second read path, so it belongs alongside S6/S7 rather than being forbidden.
RAW_ALLOWED = (
    "pipeline/stages/s6_volume_assembly",
    "pipeline/stages/s7_deface",
    "pipeline/stages/s11_export",
    # names `volume_nifti` as the PUBLISHED contract's artifact_kind (DATA-MODEL.md) -- metadata
    # about the contract, never a read of any volume
    "pipeline/catalog/contracts.py",
)

# Each producer stage's own internals are off-limits to every stage strictly AFTER it (never to
# itself -- a stage's service legitimately imports its own algorithm/segmentation module).
_S9_PLUS = ("s9_surface_extraction", "s10_mesh_postprocess", "s11_export", "s12_serve")
_S10_PLUS = ("s10_mesh_postprocess", "s11_export", "s12_serve")
_S11_PLUS = ("s11_export", "s12_serve")
GUARDS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "s6/s7 volume producers",
        ("s8_segment", *_S9_PLUS),
        (
            "pipeline.stages.s6_volume_assembly.service",
            "pipeline.stages.s7_deface.service",
            "pipeline.stages.s7_deface.algorithm",
        ),
    ),
    (
        "s8 mask producer",
        _S9_PLUS,
        ("pipeline.stages.s8_segment.service", "pipeline.stages.s8_segment.segmentation"),
    ),
    (
        # mesh_io is deliberately NOT forbidden: like nifti_io for S6-S8, it is a generic
        # reader for the format the upstream stage happens to write, not stage-specific
        # algorithm/orchestration logic. S10 legitimately reuses it to read S9's raw mesh.
        "s9 mesh producer",
        _S10_PLUS,
        (
            "pipeline.stages.s9_surface_extraction.service",
            "pipeline.stages.s9_surface_extraction.mesh_extract",
        ),
    ),
    (
        "s10 mesh producer",
        _S11_PLUS,
        (
            "pipeline.stages.s10_mesh_postprocess.service",
            "pipeline.stages.s10_mesh_postprocess.mesh_postprocess",
        ),
    ),
)
DOWNSTREAM = tuple(f"stages/{d}" for d in ("s8_segment", *_S9_PLUS))


def _rel(path: Path) -> str:
    return path.as_posix().split("/src/", 1)[1]


def _raw_name_hits(source: str) -> list[str]:
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            hits += [n for n in RAW_NAMES if n in node.value]
    return sorted(set(hits))


def _module_hits(source: str, forbidden: tuple[str, ...]) -> list[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return sorted(m for m in found if m in forbidden)


def _py_files(*roots: Path) -> list[Path]:
    return sorted(p for root in roots for p in root.rglob("*.py"))


def test_only_s6_and_s7_reference_the_raw_volume_names() -> None:
    offenders = {}
    for path in _py_files(SRC, API):
        rel = _rel(path)
        if rel.startswith(RAW_ALLOWED):
            continue
        hits = _raw_name_hits(path.read_text(encoding="utf-8"))
        if hits:
            offenders[rel] = hits
    # catalog/orchestration/cli may name artifact kinds in docs, never as string constants
    assert offenders == {}, offenders


@pytest.mark.parametrize("name, dirs, forbidden", GUARDS, ids=[g[0] for g in GUARDS])
def test_stages_strictly_downstream_of_a_producer_never_import_its_internals(
    name: str, dirs: tuple[str, ...], forbidden: tuple[str, ...]
) -> None:
    offenders = {}
    guarded_dirs = [SRC / d for d in dirs if (SRC / d).exists()]
    for path in _py_files(*guarded_dirs, API):
        hits = _module_hits(path.read_text(encoding="utf-8"), forbidden)
        if hits:
            offenders[_rel(path)] = hits
    assert offenders == {}, offenders


def test_the_downstream_directories_this_test_guards_exist() -> None:
    for d in DOWNSTREAM:
        assert (SRC / d).is_dir(), f"{d} moved -- update the guard"


@pytest.mark.parametrize(
    "source, expected",
    [
        ('x = "s/volume.nii.gz"', ["volume.nii.gz"]),
        ('kind = "volume_nifti"', ["volume_nifti"]),
        ('f"{a}/{k}" if k == "volume_defaced" else 0', ["volume_defaced"]),
        ("x = 1", []),
    ],
)
def test_the_raw_name_scanner_can_fail(source: str, expected: list[str]) -> None:
    assert _raw_name_hits(source) == expected


_S6_S7_FORBIDDEN = GUARDS[0][2]


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "from pipeline.stages.s7_deface.service import deface_series",
            ["pipeline.stages.s7_deface.service"],
        ),
        (
            "from pipeline.stages.s7_deface import algorithm",
            ["pipeline.stages.s7_deface.algorithm"],
        ),
        (
            "import pipeline.stages.s6_volume_assembly.service",
            ["pipeline.stages.s6_volume_assembly.service"],
        ),
        ("from pipeline.stages.s7_deface.gate import open_defacing_gate", []),
    ],
)
def test_the_module_scanner_can_fail(source: str, expected: list[str]) -> None:
    assert _module_hits(source, _S6_S7_FORBIDDEN) == expected


def test_the_module_scanner_catches_a_reach_into_s8s_own_internals() -> None:
    forbidden = GUARDS[1][2]
    assert _module_hits(
        "from pipeline.stages.s8_segment.service import segment_series", forbidden
    ) == ["pipeline.stages.s8_segment.service"]
    assert _module_hits("from pipeline.stages.s8_segment.gate import x", forbidden) == []


def test_s8_itself_is_exempt_from_its_own_guard_but_not_from_s6_s7s() -> None:
    """A stage's service legitimately imports its own algorithm/segmentation module -- only
    stages strictly AFTER it are forbidden from reaching into it directly."""
    s6_s7_guard_dirs, s8_guard_dirs = GUARDS[0][1], GUARDS[1][1]
    assert "s8_segment" in s6_s7_guard_dirs
    assert "s8_segment" not in s8_guard_dirs


def test_the_module_scanner_catches_a_reach_into_s9s_own_internals() -> None:
    forbidden = GUARDS[2][2]
    assert _module_hits(
        "from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh", forbidden
    ) == ["pipeline.stages.s9_surface_extraction.mesh_extract"]
    assert _module_hits("from pipeline.stages.s9_surface_extraction import params", forbidden) == []


def test_s9_itself_is_exempt_from_its_own_guard_but_not_from_s6_s7_s8s() -> None:
    s6_s7_dirs, s8_dirs, s9_dirs = GUARDS[0][1], GUARDS[1][1], GUARDS[2][1]
    assert "s9_surface_extraction" in s6_s7_dirs
    assert "s9_surface_extraction" in s8_dirs
    assert "s9_surface_extraction" not in s9_dirs


def test_the_module_scanner_catches_a_reach_into_s10s_own_internals() -> None:
    forbidden = GUARDS[3][2]
    assert _module_hits(
        "from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh",
        forbidden,
    ) == ["pipeline.stages.s10_mesh_postprocess.mesh_postprocess"]
    # mesh_io is deliberately allowed -- a future S11 legitimately reads S10's mesh through it
    assert _module_hits("from pipeline.stages.s10_mesh_postprocess import mesh_io", forbidden) == []


def test_s10_itself_is_exempt_from_its_own_guard_but_not_from_s6_s9s() -> None:
    s6_s7_dirs, s8_dirs, s9_dirs, s10_dirs = (
        GUARDS[0][1],
        GUARDS[1][1],
        GUARDS[2][1],
        GUARDS[3][1],
    )
    assert "s10_mesh_postprocess" in s6_s7_dirs
    assert "s10_mesh_postprocess" in s8_dirs
    assert "s10_mesh_postprocess" in s9_dirs
    assert "s10_mesh_postprocess" not in s10_dirs

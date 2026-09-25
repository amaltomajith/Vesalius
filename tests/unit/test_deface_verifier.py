"""The independent efficacy verifier: it must PASS the real algorithm's output on every
orientation and FAIL each specific kind of wrong output. A verifier that cannot fail proves
nothing, so most tests here hand it a deliberately broken `after` volume.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from pipeline.stages.s7_deface import params
from pipeline.stages.s7_deface.algorithm import deface_region
from pipeline.stages.s7_deface.verify import verify_defacing
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import AIR_VALUE, defaced, index_of, phantom, world_axis

VARIANTS = sorted(hp.ROTATIONS)
BASE = "lps_native_default"
VERIFY_SRC = Path(__file__).parents[2] / "src/pipeline/stages/s7_deface/verify.py"


def _verify(before: np.ndarray, after: np.ndarray, name: str = BASE):  # type: ignore[no-untyped-def]
    return verify_defacing(before, after, phantom(name).grid.affine_ras, air_value=AIR_VALUE)


@pytest.mark.parametrize("name", VARIANTS)
def test_it_passes_the_algorithms_output_on_every_orientation(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    report = _verify(ph.volume, res.defaced, name)
    assert report.failures == []
    assert report.passed
    m = report.metrics
    assert m["bone_voxels_changed"] == 0
    assert m["changed_outside_zone"] == 0
    assert m["changed_deeper_than_strip"] == 0
    assert m["skin_destroyed_fraction"] >= params.MIN_SKIN_DESTROYED_FRACTION
    assert m["anterior_retreat_mm"] >= params.MIN_ANTERIOR_RETREAT_MM


def test_nothing_changed_is_a_failure_not_a_pass() -> None:
    ph = phantom(BASE)
    assert _verify(ph.volume, ph.volume.copy()).failures == ["nothing_changed"]


def test_a_shape_mismatch_fails() -> None:
    ph = phantom(BASE)
    assert _verify(ph.volume, ph.volume[:-1].copy()).failures == ["shape_mismatch"]


def test_a_defacer_working_on_the_wrong_side_of_the_head_is_caught_by_the_verifier_alone() -> None:
    """Mirror the anterior axis in the affine the ALGORITHM sees (posterior is treated as the
    face), then verify against the TRUE affine -- the algorithm's own region cannot catch this."""
    ph = phantom(BASE)
    wrong = ph.grid.affine_ras.copy()
    wrong[1, :] = -wrong[1, :]
    res = deface_region(ph.volume, wrong, air_value=AIR_VALUE)
    report = _verify(ph.volume, res.defaced)
    assert not report.passed
    assert "changed_outside_zone" in report.failures
    assert "anterior_retreat_too_small" in report.failures


def test_a_changed_bone_voxel_fails() -> None:
    ph, res = phantom(BASE), defaced(BASE)
    after = res.defaced.copy()
    k, j, i = index_of(ph.grid, (0.0, 80.0, -35.0))  # inside the facial bone block
    assert ph.volume[k, j, i] >= params.BONE_FLOOR_HU
    after[k, j, i] = AIR_VALUE
    assert "bone_changed" in _verify(ph.volume, after).failures


def test_a_changed_voxel_with_the_wrong_removal_value_fails() -> None:
    ph, res = phantom(BASE), defaced(BASE)
    after = res.defaced.copy()
    after[res.changed_mask] = -1024.0
    assert "changed_voxel_not_removal_value" in _verify(ph.volume, after).failures


def test_removing_deep_tissue_beyond_the_strip_fails() -> None:
    ph, res = phantom(BASE), defaced(BASE)
    after = res.defaced.copy()
    k, j, i = index_of(ph.grid, (0.0, 40.0, 0.0))  # brain, ~50 mm below the scalp, inside the zone
    assert ph.volume[k, j, i] == hp.BRAIN
    after[k, j, i] = AIR_VALUE
    assert _verify(ph.volume, after).failures == ["changed_deeper_than_strip"]


def test_a_change_outside_the_zone_fails() -> None:
    ph, res = phantom(BASE), defaced(BASE)
    after = res.defaced.copy()
    k, j, i = index_of(ph.grid, (0.0, -60.0, 0.0))  # back of the head: skin-adjacent, far from face
    y = world_axis(ph.grid, 1)
    assert y[k, j, i] == pytest.approx(-60.0, abs=1.0)
    after[k, j, i] = AIR_VALUE
    assert "changed_outside_zone" in _verify(ph.volume, after).failures


def test_a_shallow_defacing_that_leaves_the_nose_fails_the_retreat_check() -> None:
    ph = phantom(BASE)
    res = deface_region(ph.volume, ph.grid.affine_ras, air_value=AIR_VALUE, strip_depth_mm=3.0)
    report = _verify(ph.volume, res.defaced)
    assert "anterior_retreat_too_small" in report.failures
    assert report.metrics["anterior_retreat_mm"] < params.MIN_ANTERIOR_RETREAT_MM


def test_a_defacing_that_removes_half_the_skin_fails_the_destroyed_fraction() -> None:
    ph, res = phantom(BASE), defaced(BASE)
    after = ph.volume.copy()
    keep_half = res.changed_mask.copy()
    keep_half[:, :, ::2] = False  # only every other column is actually removed
    after[keep_half] = AIR_VALUE
    report = _verify(ph.volume, after)
    assert "skin_not_destroyed" in report.failures
    assert 0.3 < report.metrics["skin_destroyed_fraction"] < 0.7


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


FORBIDDEN = ("s7_deface.algorithm", "s7_deface.geometry")


def _violations(source: str) -> list[str]:
    return sorted(m for m in _imports(source) if any(f in m for f in FORBIDDEN))


def test_the_verifier_imports_nothing_from_the_algorithm_or_geometry() -> None:
    assert _violations(VERIFY_SRC.read_text(encoding="utf-8")) == []


def test_the_independence_scanner_can_actually_fail() -> None:
    from_module = "from pipeline.stages.s7_deface.geometry import WorldFrame"
    assert _violations(from_module) == [
        "pipeline.stages.s7_deface.geometry",
        "pipeline.stages.s7_deface.geometry.WorldFrame",
    ]
    # `from package import submodule` and plain `import` forms are caught too
    assert _violations("from pipeline.stages.s7_deface import algorithm") == [
        "pipeline.stages.s7_deface.algorithm"
    ]
    assert _violations("import pipeline.stages.s7_deface.algorithm") == [
        "pipeline.stages.s7_deface.algorithm"
    ]
    assert _violations("import numpy\nfrom pipeline.stages.s7_deface import params") == []

"""S8 against the analytic head phantom (shared with S7's tests): orientation-independence and
the project's thesis -- segmenting the `skin_surface` after defacing is where the removed face
actually becomes visible as a shrunken mask, downstream of S7's own voxel-level checks.

Same caveat as S7's phantom tests: this shows the algorithm behaves as designed and is
orientation-independent. It is not evidence of segmentation quality on real, noisy anatomy.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s8_segment import params
from pipeline.stages.s8_segment.segmentation import segment
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import defaced, phantom, world_axis

VARIANTS = sorted(hp.ROTATIONS)


def _segment(volume: np.ndarray, preset: str):  # type: ignore[no-untyped-def]
    lo, hi = params.preset_bounds(preset)
    return segment(
        volume,
        lo=lo,
        hi=hi,
        closing_iterations=params.CLOSING_ITERATIONS,
        opening_iterations=params.OPENING_ITERATIONS,
        connectivity=params.CONNECTIVITY,
    )


@pytest.mark.parametrize("name", VARIANTS)
def test_the_bone_mask_closely_matches_the_analytic_bone_truth(name: str) -> None:
    ph = phantom(name)
    r = _segment(ph.volume, "bone")
    truth = ph.truth["bone"]
    intersection = int((r.mask & truth).sum())
    union = int((r.mask | truth).sum())
    assert union > 0
    assert intersection / union > 0.99  # cleanup trims only a thin sliver of the skull shell


def test_the_bone_mask_is_the_same_physical_object_on_every_orientation() -> None:
    counts = {n: int(_segment(phantom(n).volume, "bone").mask.sum()) for n in VARIANTS}
    base = counts[VARIANTS[0]]
    for n, c in counts.items():
        assert abs(c - base) / base < 0.02, (n, counts)


@pytest.mark.parametrize("name", VARIANTS)
def test_bone_survives_defacing_bit_for_bit_so_its_mask_is_unaffected(name: str) -> None:
    """S7 never touches bone; the bone mask before and after defacing must therefore be
    identical -- a cross-stage consistency check S7's own tests cannot express."""
    ph, res = phantom(name), defaced(name)
    before = _segment(ph.volume, "bone")
    after = _segment(res.defaced, "bone")
    assert np.array_equal(before.mask, after.mask)


def test_thesis_the_skin_surface_mask_shows_the_defaced_face_is_actually_gone() -> None:
    """CLAUDE.md's thesis in one assertion: de-identifying metadata is not de-identifying
    output. The un-defaced phantom's `skin_surface` mask reaches out to the nose tip; the
    defaced phantom's retreats to the facial skeleton -- visible in THIS stage's own mask, not
    just in S7's voxel-level checks."""
    ph = phantom("lps_native_default")
    res = defaced("lps_native_default")
    y = world_axis(ph.grid, 1)

    before = _segment(ph.volume, "skin_surface")
    after = _segment(res.defaced, "skin_surface")

    front_before = float(y[before.mask].max())
    front_after = float(y[after.mask].max())
    assert front_before == pytest.approx(123.0, abs=2.0)  # the nose tip
    assert front_after == pytest.approx(88.0, abs=3.0)  # the facial bone block
    assert front_before - front_after > 30.0

    nose = ph.truth["nose_only"]
    assert before.mask[nose].mean() > 0.9  # the un-defaced mask covers the nose
    assert after.mask[nose].mean() < 0.05  # the defaced mask does not


def test_the_bone_mesh_relevant_preset_barely_reaches_into_the_removed_soft_tissue() -> None:
    """A sanity check for S9/S10's later choice of preset: even on the UN-defaced phantom, the
    `bone` preset (unlike `skin_surface`) was never going to reconstruct a face -- no facial
    soft tissue is within its HU range at all (verified on the raw threshold, before any
    cleanup); closing's 2-voxel dilation can bridge the narrow gap to nearby face soft tissue
    at the mask level, but only a sliver (<1% of it), not the face."""
    ph = phantom("lps_native_default")
    lo, hi = params.preset_bounds("bone")
    raw = (ph.volume >= lo) & (ph.volume <= hi)
    assert not raw[ph.truth["face_soft"]].any()
    assert not raw[ph.truth["nose_only"]].any()

    r = _segment(ph.volume, "bone")
    assert r.mask[ph.truth["face_soft"]].mean() < 0.01
    assert not r.mask[ph.truth["nose_only"]].any()

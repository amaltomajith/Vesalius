"""S8's pure `segment()`: threshold bounds, cleanup order/counts, the border trap, and the
sanity-gate inputs (empty / >90% coverage) -- against hand-built volumes, no infra.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.common.hu_presets import PRESETS_V1
from pipeline.stages.s8_segment.segmentation import segment


def _vol(shape: tuple[int, int, int], value: float = -1000.0) -> np.ndarray:
    return np.full(shape, value, dtype=np.float32)


@pytest.mark.parametrize("preset", ["bone", "bone_dense", "soft_tissue", "lung", "skin_surface"])
def test_threshold_bounds_are_inclusive_and_match_the_preset_table(preset: str) -> None:
    lo, hi = float(PRESETS_V1[preset]["min"]), float(PRESETS_V1[preset]["max"])
    v = _vol((6, 6, 6))
    v[1, 1, 1] = lo - 1.0
    v[1, 1, 2] = lo
    v[1, 1, 3] = hi
    v[1, 1, 4] = hi + 1.0
    r = segment(v, lo=lo, hi=hi, closing_iterations=0, opening_iterations=0)
    assert not r.mask[1, 1, 1]
    assert r.mask[1, 1, 2] and r.mask[1, 1, 3]
    assert not r.mask[1, 1, 4]


def test_closing_seals_a_small_gap_inside_a_cube() -> None:
    v = _vol((14, 14, 14))
    v[2:12, 2:12, 2:12] = 400.0
    v[6, 6, 6] = -1000.0  # a 1-voxel gap well inside the cube -- easily sealed
    r = segment(v, lo=300, hi=3000, closing_iterations=2, opening_iterations=0)
    assert r.mask[6, 6, 6]
    assert r.stats["final_voxels"] == 10**3


def test_opening_drops_a_speckle_and_keeps_the_bulk_of_a_solid_cube() -> None:
    """A radius-1 cross structuring element (GEOMETRY.md's default, unspecified `structure=`)
    rounds off a cube's corners/edges -- real morphology, not a bug -- so the whole cube does
    NOT survive bit for bit. What must hold: the isolated speckle is gone, the interior and
    every flat face centre survive, and the vast majority of the cube remains."""
    v = _vol((20, 20, 20))
    v[5:15, 5:15, 5:15] = 400.0  # a solid 10^3 cube
    v[1, 1, 1] = 400.0  # an isolated single voxel
    r = segment(v, lo=300, hi=3000, closing_iterations=0, opening_iterations=1)
    assert not r.mask[1, 1, 1]
    assert r.mask[10, 10, 10]  # centre
    assert r.mask[5, 10, 10] and r.mask[10, 5, 10] and r.mask[10, 10, 5]  # face centres
    assert not r.mask[5, 5, 5]  # a true 3-D corner is legitimately rounded off
    assert r.stats["final_voxels"] > 0.85 * 1000


def test_largest_component_is_kept_and_the_smaller_one_is_dropped() -> None:
    v = _vol((20, 20, 30))
    v[2:10, 2:10, 2:10] = 400.0  # 8^3 = 512
    v[2:6, 2:6, 20:24] = 400.0  # 4^3 = 64, far away
    r = segment(v, lo=300, hi=3000, closing_iterations=0, opening_iterations=0)
    assert r.mask[2:10, 2:10, 2:10].all()
    assert not r.mask[2:6, 2:6, 20:24].any()
    assert r.stats["component_count"] == 2
    assert r.stats["largest_component_voxels"] == 512


def test_6_connectivity_treats_diagonal_touching_cubes_as_separate_components() -> None:
    v = _vol((10, 10, 10))
    v[0:3, 0:3, 0:3] = 400.0  # 27
    v[3:7, 3:7, 3:7] = 400.0  # 64, touching only at the (3,3,3) corner
    r = segment(v, lo=300, hi=3000, closing_iterations=0, opening_iterations=0, connectivity=6)
    assert r.stats["component_count"] == 2
    assert r.stats["largest_component_voxels"] == 64
    assert not r.mask[0:3, 0:3, 0:3].any()


def test_26_connectivity_joins_the_same_diagonal_touching_cubes() -> None:
    v = _vol((10, 10, 10))
    v[0:3, 0:3, 0:3] = 400.0
    v[3:7, 3:7, 3:7] = 400.0
    r = segment(v, lo=300, hi=3000, closing_iterations=0, opening_iterations=0, connectivity=26)
    assert r.stats["component_count"] == 1
    assert r.stats["final_voxels"] == 27 + 64


def test_an_unsupported_connectivity_raises() -> None:
    with pytest.raises(ValueError, match="connectivity"):
        segment(
            _vol((4, 4, 4)), lo=0, hi=1, closing_iterations=0, opening_iterations=0, connectivity=18
        )


def test_empty_threshold_yields_an_empty_mask_and_zero_stats() -> None:
    v = _vol((5, 5, 5))
    r = segment(v, lo=300, hi=3000, closing_iterations=2, opening_iterations=1)
    assert not r.mask.any()
    assert r.stats["final_voxels"] == 0
    assert r.stats["coverage_fraction"] == 0.0
    assert r.stats["bbox_zyx"] is None


def test_a_mask_covering_the_whole_volume_reports_full_coverage() -> None:
    v = _vol((5, 5, 5), value=400.0)
    r = segment(v, lo=300, hi=3000, closing_iterations=0, opening_iterations=0)
    assert r.stats["coverage_fraction"] == pytest.approx(1.0)


def test_the_input_volume_is_never_mutated() -> None:
    v = _vol((6, 6, 6))
    v[2:4, 2:4, 2:4] = 400.0
    before = v.copy()
    segment(v, lo=300, hi=3000, closing_iterations=2, opening_iterations=1)
    assert np.array_equal(v, before)


# --- the border trap: characterised and documented, NOT compensated for (see segmentation.py) --


def test_a_structure_touching_the_true_array_edge_is_eroded_there_by_cleanup() -> None:
    """Documents the accepted limitation: `border_value=0` treats outside-the-array as
    background, so a structure flush against the true edge loses voxels there entirely --
    unlike the exact same structure given real neighbours to interact with (next test). A
    mutant that "fixes" this silently (e.g. by padding) would change these numbers -- that is
    the point of pinning them down."""
    v = _vol((10, 10, 10))
    v[:, 2:8, 2:8] = 400.0  # a slab spanning the full k-axis: touches k=0 and k=9
    r = segment(v, lo=300, hi=3000, closing_iterations=2, opening_iterations=1)
    assert r.mask[0, 2:8, 2:8].sum() == 0  # the touching face is wiped out entirely
    assert r.mask[9, 2:8, 2:8].sum() == 0
    # an interior slice, far from either true edge, is unaffected by the boundary
    assert r.mask[4, 3:7, 3:7].all()


def test_the_same_slab_with_headroom_keeps_more_of_its_own_flat_face_than_the_touching_case() -> (
    None
):
    """Companion: give the identical object clearance from the true array edge and its own
    (now interior) top/bottom faces keep substantially more than the touching case's zero --
    confirming the erosion above is a real edge-of-array artefact, not an inherent property of
    the shape or of running closing then opening."""
    shape = (10, 10, 10)
    pad = 4
    v = _vol((shape[0] + 2 * pad, shape[1], shape[2]))
    v[pad : pad + shape[0], 2:8, 2:8] = 400.0
    r = segment(v, lo=300, hi=3000, closing_iterations=2, opening_iterations=1)
    assert r.mask[pad, 2:8, 2:8].sum() > 0  # its own flat face, now interior, is not wiped out
    assert r.mask[pad + shape[0] - 1, 2:8, 2:8].sum() > 0
    assert not r.mask[:pad].any() and not r.mask[pad + shape[0] :].any()  # headroom stays empty


def test_closing_then_opening_is_not_the_same_as_opening_then_closing() -> None:
    """GEOMETRY.md's order is closing THEN opening -- pins down that the order is load-bearing,
    not an arbitrary choice, with a shape where the two operations interact: two blobs joined by
    a 1-voxel-wide neck (opening alone would sever it) plus a small gap inside one blob (closing
    alone would seal it). Closing first seals the gap and partially protects the neck (the
    dilation half thickens it before opening's erosion runs); opening first erodes the neck down
    before closing ever sees it. The two orders must disagree here, or this test is not
    checking what it claims to."""
    shape = (20, 20, 20)
    m = np.zeros(shape, dtype=bool)
    m[2:7, 2:7, 2:7] = True  # blob A
    m[13:18, 2:7, 2:7] = True  # blob B
    m[7:13, 4, 4] = True  # a 1-voxel-wide neck joining them
    m[4, 4, 4] = False  # a 1-voxel gap inside blob A

    from pipeline.stages.s8_segment.segmentation import _clean

    closing_first = _clean(m, closing_iterations=2, opening_iterations=1)
    opening_first = m.copy()
    from scipy import ndimage

    opening_first = ndimage.binary_opening(opening_first, iterations=1)
    opening_first = ndimage.binary_closing(opening_first, iterations=2)

    assert not np.array_equal(closing_first, opening_first)
    assert closing_first.sum() != opening_first.sum()
    assert closing_first[4, 4, 4]  # the gap is sealed either way
    # `_clean` is exactly the step `segment()` calls between thresholding and largest-component
    # selection -- this pins its own behaviour down, independent of that later step


@pytest.mark.parametrize("closing,opening", [(0, 0), (3, 0), (0, 2), (4, 3)])
def test_any_cleanup_iteration_count_runs_without_crashing_on_a_fully_tissue_volume(
    closing: int, opening: int
) -> None:
    """A volume that is entirely tissue touches the true edge on every face, so it is exactly
    where the border trap (see segmentation.py) bites hardest -- worth confirming it still
    completes cleanly and produces a sane, monotonically-bounded result rather than crashing."""
    v = _vol((30, 30, 30), value=400.0)
    r = segment(v, lo=300, hi=3000, closing_iterations=closing, opening_iterations=opening)
    if closing == opening == 0:
        assert r.mask.all()
    else:
        assert 0 <= r.stats["final_voxels"] <= 30**3
        assert r.mask[15, 15, 15]  # the volume's centre, far from every edge, always survives

"""`geometric_v1` against the analytic head phantom rendered on five orientations.

The phantom proves the algorithm behaves as DESIGNED and does so independent of orientation. It
does NOT prove it defeats face recognition on real anatomy (see head_phantom.py's docstring).
Expectations are written in world space with test-owned coordinate math.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from pipeline.common.errors import (
    DefaceAffineInvalidError,
    DefaceChangeImplausibleError,
    DefaceNoEffectError,
    NoHeadFoundError,
)
from pipeline.stages.s7_deface import params
from pipeline.stages.s7_deface.algorithm import body_mask, deface_region
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import AIR_VALUE, defaced, phantom, world_axis

VARIANTS = sorted(hp.ROTATIONS)
BASE = "lps_native_default"


@pytest.mark.parametrize("name", VARIANTS)
def test_the_nose_has_no_bone_behind_it_so_it_is_removed(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    nose = ph.truth["nose_only"]
    assert nose.sum() > 1000
    assert np.count_nonzero(res.defaced[nose] >= params.TISSUE_MIN_HU) / nose.sum() <= 0.01


@pytest.mark.parametrize("name", VARIANTS)
def test_the_front_of_the_head_retreats_to_the_facial_skeleton(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    y = world_axis(ph.grid, 1)
    before = y[ph.volume >= params.TISSUE_MIN_HU].max()
    after = y[res.defaced >= params.TISSUE_MIN_HU].max()
    assert before == pytest.approx(123.0, abs=1.0)  # nose tip, analytic
    assert after == pytest.approx(88.0, abs=2.5)  # front of the facial bone block, analytic
    assert before - after >= params.MIN_ANTERIOR_RETREAT_MM


@pytest.mark.parametrize("name", VARIANTS)
def test_bone_brain_sinus_and_skull_base_are_bit_identical(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    for key in ("bone", "brain", "sinus", "skull_base"):
        m = ph.truth[key]
        assert m.any(), key
        assert np.array_equal(res.defaced[m], ph.volume[m]), key


@pytest.mark.parametrize("name", VARIANTS)
def test_nothing_outside_the_region_changes_and_the_region_is_the_world_slab(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    y, z = world_axis(ph.grid, 1), world_axis(ph.grid, 2)
    body = ph.volume >= params.TISSUE_MIN_HU
    a_hi, a_lo = y[body].max(), y[body].min()
    a_min = a_hi - params.ANTERIOR_FRACTION * (a_hi - a_lo)
    s_hi, s_lo = z[body].max(), z[body].min()
    s_min = s_hi - min(params.SUPERIOR_FRACTION * (s_hi - s_lo), params.HEAD_HEIGHT_CAP_MM)
    # the region is the analytic world slab, to within a voxel at each face
    assert res.region_mask[(y >= a_min + 1.0) & (z >= s_min + 1.0)].all()
    assert not res.region_mask[y < a_min - 1.0].any()
    assert not res.region_mask[z < s_min - 1.0].any()
    # ... and nothing outside it is touched, bit for bit
    assert np.array_equal(res.defaced[~res.region_mask], ph.volume[~res.region_mask])
    assert not (res.changed_mask & ~res.region_mask).any()


@pytest.mark.parametrize("name", VARIANTS)
def test_every_changed_voxel_becomes_the_removal_value(name: str) -> None:
    ph, res = phantom(name), defaced(name)
    changed = res.defaced != ph.volume
    assert np.array_equal(changed, res.changed_mask)
    assert (res.defaced[changed] == AIR_VALUE).all()


def test_the_removal_value_is_the_argument_not_a_hardcoded_air() -> None:
    ph = phantom(BASE)
    res = deface_region(ph.volume, ph.grid.affine_ras, air_value=-1024.0)
    assert (res.defaced[res.changed_mask] == -1024.0).all()
    assert res.changed_mask.sum() == defaced(BASE).changed_mask.sum()


def test_every_orientation_removes_the_same_physical_object() -> None:
    counts = {n: int(defaced(n).changed_mask.sum()) for n in VARIANTS}
    base = counts[BASE]
    for n, c in counts.items():
        assert abs(c - base) / base < 0.01, (n, counts)


def test_the_input_volume_is_never_mutated_and_the_output_is_deterministic() -> None:
    ph = phantom(BASE)
    volume = ph.volume.copy()
    a = deface_region(volume, ph.grid.affine_ras, air_value=AIR_VALUE)
    b = deface_region(volume, ph.grid.affine_ras, air_value=AIR_VALUE)
    assert np.array_equal(volume, ph.volume)
    assert a.defaced is not volume
    assert (
        hashlib.sha256(a.defaced.tobytes()).digest() == hashlib.sha256(b.defaced.tobytes()).digest()
    )


def test_the_neck_and_shoulders_beyond_the_height_cap_are_untouched() -> None:
    ph = phantom(BASE, True)
    res = deface_region(ph.volume, ph.grid.affine_ras, air_value=AIR_VALUE)
    z = world_axis(ph.grid, 2)
    top = z[ph.volume >= params.TISSUE_MIN_HU].max()
    below_cap = z < top - params.HEAD_HEIGHT_CAP_MM
    assert below_cap.any()
    assert not (res.changed_mask & below_cap).any()
    shoulders = below_cap & (ph.volume >= params.TISSUE_MIN_HU)
    assert shoulders.sum() > 100_000
    assert np.array_equal(res.defaced[shoulders], ph.volume[shoulders])
    assert res.changed_mask.sum() > 200_000  # and the face is still removed


def test_body_mask_keeps_the_nose_tip_that_a_plain_opening_would_trim() -> None:
    ph = phantom(BASE)
    body = body_mask(ph.volume, params.TISSUE_MIN_HU)
    assert body[ph.truth["nose_only"]].all()
    # ... while still discarding a lone speckle far from the head
    vol = ph.volume.copy()
    vol[3, 3, 3] = 40.0
    assert not body_mask(vol, params.TISSUE_MIN_HU)[3, 3, 3]


def _box_body(
    shape: tuple[int, int, int], lo: tuple[int, int, int], hi: tuple[int, int, int]
) -> np.ndarray:
    v = np.full(shape, -1000.0, dtype=np.float32)
    v[lo[0] : hi[0], lo[1] : hi[1], lo[2] : hi[2]] = 40.0
    return v


def test_a_chest_sized_body_is_refused_as_no_head_by_width_alone() -> None:
    # 310 mm wide, 190 mm AP, 150 mm tall, lots of bone: every OTHER sanity check passes
    v = _box_body((160, 200, 320), (5, 5, 5), (155, 195, 315))
    v[40:120, 40:160, 60:260] = 1000.0
    with pytest.raises(NoHeadFoundError) as exc:
        deface_region(v, np.eye(4), air_value=AIR_VALUE)
    assert exc.value.detail["failed"] == ["head_width_out_of_range"]


def test_an_empty_or_tiny_volume_is_refused_as_no_head() -> None:
    with pytest.raises(NoHeadFoundError):
        deface_region(np.full((20, 20, 20), -1000.0, np.float32), np.eye(4), air_value=AIR_VALUE)
    tiny = _box_body((40, 40, 40), (10, 10, 10), (20, 20, 20))
    with pytest.raises(NoHeadFoundError) as exc:
        deface_region(tiny, np.eye(4), air_value=AIR_VALUE)
    assert "body_volume_too_small" in exc.value.detail["failed"]


@pytest.mark.parametrize(
    "affine",
    [
        np.diag([1.0, 2.0, 1.0, 1.0]),  # anisotropic
        np.diag([2.0, 2.0, 2.0, 1.0]),  # wrong spacing for spacing_mm=1
    ],
)
def test_a_non_isotropic_or_mis_scaled_affine_is_refused(affine: np.ndarray) -> None:
    with pytest.raises(DefaceAffineInvalidError):
        deface_region(phantom(BASE).volume, affine, air_value=AIR_VALUE)


def test_a_non_float_or_non_finite_volume_is_refused() -> None:
    ph = phantom(BASE)
    with pytest.raises(DefaceAffineInvalidError):
        deface_region(ph.volume.astype(np.int16), ph.grid.affine_ras, air_value=AIR_VALUE)
    bad = ph.volume.copy()
    bad[0, 0, 0] = np.nan
    with pytest.raises(DefaceAffineInvalidError):
        deface_region(bad, ph.grid.affine_ras, air_value=AIR_VALUE)


def test_a_run_that_changes_too_little_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(params, "MIN_CHANGED_VOXELS", 10**9)
    ph = phantom(BASE)
    with pytest.raises(DefaceNoEffectError):
        deface_region(ph.volume, ph.grid.affine_ras, air_value=AIR_VALUE)


def test_a_run_that_changes_too_much_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(params, "MAX_CHANGED_FRACTION_OF_BODY", 0.001)
    ph = phantom(BASE)
    with pytest.raises(DefaceChangeImplausibleError):
        deface_region(ph.volume, ph.grid.affine_ras, air_value=AIR_VALUE)


def test_a_detached_object_in_front_of_the_face_is_left_alone() -> None:
    """Tissue that is not part of the body (a headrest, a mask, a tube) may lie inside the region
    slab; only the body's own soft tissue is stripped."""
    bbox = ((-75.0, 75.0), (-98.0, 175.0), (-105.0, 105.0))
    ph = hp.render(hp.grid_for(hp.ROTATIONS[BASE], bbox))
    x, y, z = (world_axis(ph.grid, a) for a in (0, 1, 2))
    blob = (np.abs(x) <= 8) & (y >= 150) & (y <= 166) & (np.abs(z + 10) <= 12)
    assert blob.sum() > 3000
    volume = ph.volume.copy()
    volume[blob] = 40.0
    res = deface_region(volume, ph.grid.affine_ras, air_value=AIR_VALUE)
    assert np.array_equal(res.defaced[blob], volume[blob])
    assert res.changed_mask.sum() > 200_000  # the face itself was still removed

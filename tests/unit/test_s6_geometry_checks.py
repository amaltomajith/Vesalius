"""S6 re-verifies geometry itself instead of trusting S3 (STAGES.md S6 "Fails on: non-uniform
spacing, fewer than 3 slices, mixed orientations", plus the cache cross-check). Exercises the
real `_verify_geometry`; every failure must raise, never repair (CLAUDE.md rule 6).
"""

from __future__ import annotations

import pytest

from pipeline.common.config import Settings
from pipeline.common.errors import (
    GeometryDriftError,
    OrientationMixedError,
    SpacingNonUniformError,
    TooFewSlicesError,
)
from pipeline.stages.s6_volume_assembly.service import _gather_silver_slices, _verify_geometry
from tests.fixtures.dicom_factory import build_series
from tests.fixtures.volume_asserts import make_in_memory_series


def _verify(  # type: ignore[no-untyped-def]
    z_values: list[float],
    *,
    per_slice: dict[int, dict[str, object]] | None = None,
    **series_kwargs,
):
    overrides = {k: {"image_position": (0.0, 0.0, z)} for k, z in enumerate(z_values)}
    for k, extra in (per_slice or {}).items():
        overrides[k] = {**overrides[k], **extra}
    blobs = build_series(count=len(z_values), overrides=overrides, pixel_data="valid")
    mem = make_in_memory_series(blobs, **series_kwargs)
    gathered = _gather_silver_slices(mem.client, Settings(), mem.series, mem.artifacts_by_instance)
    return _verify_geometry(mem.series, sorted(gathered, key=lambda s: s.position))


def test_clean_uniform_series_passes_and_reports_the_fresh_spacing() -> None:
    assert _verify([0.0, 2.0, 4.0, 6.0], slice_spacing=2.0) == (2.0, True)


def test_fewer_than_three_slices_fails() -> None:
    with pytest.raises(TooFewSlicesError) as exc:
        _verify([0.0, 2.0], slice_spacing=2.0)
    assert exc.value.reason_code == "S6_TOO_FEW_SLICES"
    assert exc.value.detail["instance_count"] == 2


def test_mixed_orientation_fails_with_the_offending_instance_named() -> None:
    with pytest.raises(OrientationMixedError) as exc:
        _verify(
            [0.0, 2.0, 4.0, 6.0],
            slice_spacing=2.0,
            per_slice={2: {"orientation": [0, 1, 0, -1, 0, 0]}},
        )
    assert exc.value.reason_code == "S6_ORIENTATION_MIXED"
    assert len(exc.value.detail["instances"]) == 1  # type: ignore[arg-type]


def test_a_small_orientation_wobble_within_tolerance_is_accepted() -> None:
    _verify(
        [0.0, 2.0, 4.0],
        slice_spacing=2.0,
        per_slice={1: {"orientation": [1, 0, 0, 0, 1, 0.00001]}},
    )


@pytest.mark.parametrize(
    "cached_spacing, cached_uniform",
    [(999.0, True), (2.5, True), (None, None), (2.0, False)],
    ids=["wildly-off", "slightly-off", "cache-empty", "uniformity-flag-disagrees"],
)
def test_any_disagreement_with_the_s3_cache_is_geometry_drift(
    cached_spacing: float | None, cached_uniform: bool | None
) -> None:
    with pytest.raises(GeometryDriftError) as exc:
        _verify([0.0, 2.0, 4.0, 6.0], slice_spacing=cached_spacing, spacing_uniform=cached_uniform)
    assert exc.value.reason_code == "S6_GEOMETRY_DRIFT"


def test_non_uniform_spacing_fails_even_when_the_cache_agrees_it_is_non_uniform() -> None:
    # Cache and fresh both say "non-uniform" -> no drift, but S6 must still refuse: resampling
    # would treat the median spacing as if it held everywhere.
    z = [0.0, 2.0, 4.0, 9.0]  # diffs 2, 2, 5 -> median 2.0, max deviation 150%
    with pytest.raises(SpacingNonUniformError) as exc:
        _verify(z, slice_spacing=2.0, spacing_uniform=False)
    assert exc.value.reason_code == "S6_SPACING_NONUNIFORM"
    assert exc.value.detail["slice_spacing"] == 2.0


def test_duplicate_positions_zero_spacing_fails_rather_than_dividing_by_zero() -> None:
    with pytest.raises(SpacingNonUniformError):
        _verify([1.0, 1.0, 1.0, 1.0], slice_spacing=0.0, spacing_uniform=False)

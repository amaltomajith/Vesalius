"""Slice ordering by position projection -- never InstanceNumber, never a cached value
(CLAUDE.md trap #1). Runs S6's real `_gather_silver_slices` against in-memory stand-ins for
the silver bucket and catalog. TESTING.md Part 4 names this file.
"""

from __future__ import annotations

from pipeline.common.config import Settings
from pipeline.stages.s6_volume_assembly.service import _gather_silver_slices
from tests.fixtures.dicom_factory import build_series
from tests.fixtures.volume_asserts import make_in_memory_series


def _positions_in_sorted_order(blobs: list[bytes], **series_kwargs) -> list[float]:  # type: ignore[no-untyped-def]
    mem = make_in_memory_series(blobs, **series_kwargs)
    gathered = _gather_silver_slices(mem.client, Settings(), mem.series, mem.artifacts_by_instance)
    return [s.position for s in sorted(gathered, key=lambda s: s.position)]


def _series(z_values: list[float], instance_numbers: list[int], **kwargs) -> list[bytes]:  # type: ignore[no-untyped-def]
    return build_series(
        count=len(z_values),
        overrides={
            k: {"image_position": (0.0, 0.0, z), "instance_number": n}
            for k, (z, n) in enumerate(zip(z_values, instance_numbers, strict=True))
        },
        pixel_data="valid",
        **kwargs,
    )


def test_scrambled_instance_numbers_do_not_affect_order() -> None:
    z = [0.0, 1.0, 2.0, 3.0, 4.0]
    positions = _positions_in_sorted_order(_series(z, [3, 5, 1, 4, 2]))
    assert positions == z


def test_order_is_the_same_for_either_acquisition_direction() -> None:
    z = [0.0, 2.0, 4.0, 6.0]
    ascending = _positions_in_sorted_order(_series(z, [1, 2, 3, 4]))
    descending = _positions_in_sorted_order(_series(z, [4, 3, 2, 1]))
    assert ascending == descending == z


def test_position_is_recomputed_from_the_silver_bytes_not_read_from_the_cache() -> None:
    blobs = _series([0.0, 1.0, 2.0], [1, 2, 3])
    mem = make_in_memory_series(blobs)
    for garbage, instance in zip((999.0, -5.0, 42.0), mem.series.instances, strict=True):
        instance.slice_position = garbage  # a stale/poisoned S3 cache
    gathered = _gather_silver_slices(mem.client, Settings(), mem.series, mem.artifacts_by_instance)
    assert sorted(s.position for s in gathered) == [0.0, 1.0, 2.0]


def test_a_flipped_slice_normal_sorts_by_projection_not_by_physical_z() -> None:
    # IOP [1,0,0, 0,-1,0] => normal (0,0,-1): projection = -z, so ascending projection is
    # DESCENDING physical z. The order must follow the projection.
    z = [10.0, 8.0, 6.0, 4.0]
    positions = _positions_in_sorted_order(
        _series(z, [1, 2, 3, 4], orientation=[1, 0, 0, 0, -1, 0])
    )
    assert positions == [-10.0, -8.0, -6.0, -4.0]

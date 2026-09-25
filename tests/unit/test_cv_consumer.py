"""The CV consumer: it accepts a valid volume and REFUSES every way the contract can be broken.

Invalid volumes are forged by patching one header field of a genuinely valid NIfTI, so each case
is "valid except for exactly this". Feature tests go through the RAS affine and are checked to be
identical across the five orientations of the same physical phantom.
"""

from __future__ import annotations

import copy
import gzip
import json
import struct
from collections.abc import Callable

import numpy as np
import pytest

from consumer.contract import (
    ContractViolation,
    validate_contract,
    validate_manifest,
)
from consumer.features import compute_features
from consumer.loader import load
from pipeline.catalog.contracts import VOLUME_CONTRACT_V1_0
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import nifti_bytes, phantom, world_axis

CONTRACT = VOLUME_CONTRACT_V1_0
ARRAY = (np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6) - 100.0).astype(np.float32)
IDENTITY = np.eye(4)


def _valid() -> bytes:
    return nifti_bytes(ARRAY, IDENTITY)


def _patch(nii: bytes, fn: Callable[[bytearray], None]) -> bytes:
    raw = bytearray(gzip.decompress(nii))
    fn(raw)
    return gzip.compress(bytes(raw), mtime=0)


def _set_srow_col(raw: bytearray, col: int, values: tuple[float, float, float]) -> None:
    for row, off in enumerate((280, 296, 312)):
        struct.pack_into("<f", raw, off + 4 * col, values[row])


def _contract(**changes: object) -> dict:  # type: ignore[type-arg]
    c = copy.deepcopy(CONTRACT)
    for path, value in changes.items():
        node = c
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return c


# ---- the happy path ---------------------------------------------------------------------------


def test_a_valid_volume_loads_exactly() -> None:
    v = load(_valid(), CONTRACT)
    assert v.array.shape == (4, 5, 6) and v.array.dtype == np.float32
    assert np.array_equal(v.array, ARRAY)
    assert np.allclose(v.affine_ras, IDENTITY)
    assert v.spacing_mm == (1.0, 1.0, 1.0)


@pytest.mark.parametrize("name", sorted(hp.ROTATIONS))
def test_every_oriented_phantom_grid_loads_with_its_affine(name: str) -> None:
    ph = phantom(name)
    v = load(nifti_bytes(ph.volume, ph.grid.affine_ras), CONTRACT)
    assert v.array.shape == ph.volume.shape
    assert np.array_equal(v.array, ph.volume)
    assert np.allclose(v.affine_ras, ph.grid.affine_ras, atol=1e-3)


# ---- contract claims --------------------------------------------------------------------------


def test_an_lps_contract_is_refused_and_the_message_says_ras() -> None:
    """Proof 4's shape: `cv_consumer.load(volume, contract)` with a v1.1 that says LPS."""
    lps = _contract(version="1.1", spatial__coordinate_frame="LPS")
    with pytest.raises(ContractViolation, match="RAS"):
        load(_valid(), lps)


@pytest.mark.parametrize(
    "changes, match",
    [
        ({"version": "2.0"}, "major version"),
        ({"version": "0.9"}, "major version"),
        ({"artifact_kind": "mask"}, "artifact_kind"),
        ({"format": "dicom"}, "format"),
        ({"spatial__coordinate_frame": "LPS"}, "RAS"),
        ({"spatial__coordinate_frame": "LAS"}, "RAS"),
        ({"intensity__units": "arbitrary"}, "HU"),
        ({"array__axis_order": ["x", "y", "z"]}, "axis order"),
        ({"array__dimensions": 4}, "contract declares 4"),
    ],
)
def test_a_contract_making_unsupported_claims_is_refused(changes: dict, match: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ContractViolation, match=match):
        load(_valid(), _contract(**changes))


def test_a_contract_with_no_version_is_refused() -> None:
    c = copy.deepcopy(CONTRACT)
    del c["version"]
    with pytest.raises(ContractViolation, match="no version"):
        validate_contract(c)


def test_a_minor_version_bump_is_still_supported() -> None:
    load(_valid(), _contract(version="1.7"))


# ---- the data against the contract's claims ----------------------------------------------------


def test_a_mirrored_volume_is_refused() -> None:
    """A reflected sform (determinant < 0) is the silent left-right flip the contract exists to
    prevent: the array looks fine, the world coordinates are mirrored."""
    mirrored = _patch(_valid(), lambda r: _set_srow_col(r, 0, (-1.0, 0.0, 0.0)))
    with pytest.raises(ContractViolation, match="mirrored"):
        load(mirrored, CONTRACT)


def test_a_sheared_sform_is_refused() -> None:
    sheared = _patch(_valid(), lambda r: struct.pack_into("<f", r, 280 + 4 * 1, 0.5))
    with pytest.raises(ContractViolation, match="orthonormal"):
        load(sheared, CONTRACT)


def test_an_anisotropic_volume_is_refused_when_the_contract_says_isotropic() -> None:
    def stretch(r: bytearray) -> None:
        _set_srow_col(r, 2, (0.0, 0.0, 2.0))
        struct.pack_into("<f", r, 76 + 4 * 3, 2.0)

    with pytest.raises(ContractViolation, match="isotropic"):
        load(_patch(_valid(), stretch), CONTRACT)


def test_pixdim_disagreeing_with_the_sform_is_refused() -> None:
    with pytest.raises(ContractViolation, match="pixdim"):
        load(_patch(_valid(), lambda r: struct.pack_into("<f", r, 76 + 4, 3.0)), CONTRACT)


def test_a_volume_without_an_sform_is_refused() -> None:
    with pytest.raises(ContractViolation, match="no sform"):
        load(_patch(_valid(), lambda r: struct.pack_into("<h", r, 254, 0)), CONTRACT)


def test_a_wrong_datatype_is_refused() -> None:
    with pytest.raises(ContractViolation, match="float32"):
        load(_patch(_valid(), lambda r: struct.pack_into("<h", r, 70, 4)), CONTRACT)  # int16


def test_a_wrong_spatial_unit_is_refused() -> None:
    with pytest.raises(ContractViolation, match="millimetres"):
        load(_patch(_valid(), lambda r: r.__setitem__(123, 3)), CONTRACT)  # micrometres


def test_a_four_dimensional_volume_is_refused() -> None:
    def four_d(r: bytearray) -> None:
        struct.pack_into("<h", r, 40, 4)
        struct.pack_into("<h", r, 48, 1)

    with pytest.raises(ContractViolation, match="3 dimensions"):
        load(_patch(_valid(), four_d), CONTRACT)


def test_non_finite_values_are_refused() -> None:
    bad = ARRAY.copy()
    bad[1, 2, 3] = np.nan
    with pytest.raises(ContractViolation, match="non-finite"):
        load(nifti_bytes(bad, IDENTITY), CONTRACT)


@pytest.mark.parametrize(
    "blob",
    [b"", b"not a nifti at all", gzip.compress(b"short"), gzip.compress(b"\x00" * 400)],
)
def test_unreadable_files_are_refused(blob: bytes) -> None:
    with pytest.raises(ContractViolation):
        load(blob, CONTRACT)


def test_a_truncated_payload_is_refused() -> None:
    cut = _patch(_valid(), lambda r: r.__delitem__(slice(len(r) - 40, len(r))))
    with pytest.raises(ContractViolation, match="shorter"):
        load(cut, CONTRACT)


# ---- the manifest cross-check --------------------------------------------------------------------


def _manifest(**over: object) -> dict:  # type: ignore[type-arg]
    m = {
        "contract_version": "1.0",
        "geometry": {"source_frame": "RAS_mm"},
        "deface": {"required": True, "applied": True},
    }
    m.update(over)
    return m


def test_a_consistent_manifest_is_accepted() -> None:
    load(_valid(), CONTRACT, _manifest())
    load(_valid(), CONTRACT, _manifest(deface={"required": False, "applied": False}))


def test_a_manifest_from_another_contract_version_is_refused() -> None:
    with pytest.raises(ContractViolation, match="contract"):
        validate_manifest(CONTRACT, _manifest(contract_version="1.1"))


def test_a_manifest_recording_an_lps_source_frame_is_refused() -> None:
    with pytest.raises(ContractViolation, match="RAS"):
        validate_manifest(CONTRACT, _manifest(geometry={"source_frame": "LPS_mm"}))


def test_a_head_volume_whose_manifest_says_defacing_was_skipped_is_refused() -> None:
    with pytest.raises(ContractViolation, match="defacing"):
        load(_valid(), CONTRACT, _manifest(deface={"required": True, "applied": False}))


# ---- features ---------------------------------------------------------------------------------


def _sphere(radius_vox: int = 20, spacing: float = 2.0) -> tuple[bytes, np.ndarray]:
    n = 2 * radius_vox + 11
    zz, yy, xx = np.mgrid[0:n, 0:n, 0:n]
    c = n // 2
    inside = (zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2 <= radius_vox**2
    arr = np.where(inside, 40.0, -1000.0).astype(np.float32)
    affine = np.diag([spacing, spacing, spacing, 1.0])
    affine[:3, 3] = (10.0, -20.0, 30.0)
    return nifti_bytes(arr, affine), affine


def test_features_of_an_analytic_sphere() -> None:
    blob, affine = _sphere(radius_vox=20, spacing=2.0)
    f = compute_features(load(blob, CONTRACT))
    analytic_ml = 4 / 3 * np.pi * (20 * 2.0) ** 3 / 1000.0
    assert f["body_volume_ml"] == pytest.approx(analytic_ml, rel=0.02)
    assert f["class_volume_ml"]["soft_tissue"] == f["body_volume_ml"]
    assert f["class_volume_ml"]["bone"] == 0.0
    assert f["mean_hu_body"] == 40.0 and f["std_hu_body"] == 0.0
    assert f["voxel_volume_ml"] == 0.008
    centre_index = (51 // 2,) * 3
    assert f["centroid_ras_mm"] == pytest.approx(
        list(affine[:3, :3] @ np.array(centre_index) + affine[:3, 3]), abs=0.01
    )
    ext = f["extent_ras_mm"]
    assert (np.array(ext["max"]) - np.array(ext["min"])) == pytest.approx(
        [2 * 20 * 2.0] * 3, abs=2 * 2.0 + 0.01
    )


def test_class_volumes_are_disjoint_and_never_exceed_the_array() -> None:
    f = compute_features(load(_valid(), CONTRACT))
    total_ml = ARRAY.size * f["voxel_volume_ml"]
    assert sum(f["class_volume_ml"].values()) <= total_ml + 1e-6


def test_each_hu_class_counts_exactly_its_own_voxels() -> None:
    values = {
        -1000.0: 3,  # air
        -700.0: 5,  # lung-like
        -300.0: 7,  # partial volume: in no class, but is body
        -100.0: 11,  # fat
        40.0: 13,  # soft tissue
        700.0: 17,  # bone
    }
    flat = np.concatenate([np.full(n, hu, dtype=np.float32) for hu, n in values.items()])
    arr = flat.reshape(1, 1, -1)
    f = compute_features(load(nifti_bytes(arr, np.eye(4)), CONTRACT))
    ml = f["voxel_volume_ml"]
    assert ml == 0.001
    assert f["class_volume_ml"] == {
        "air": round(3 * ml, 3),
        "lung_like": round(5 * ml, 3),
        "fat": round(11 * ml, 3),
        "soft_tissue": round(13 * ml, 3),
        "bone": round(17 * ml, 3),
    }
    assert f["body_volume_ml"] == pytest.approx(
        (7 + 11 + 13 + 17) * ml, abs=1e-3
    )  # HU > -500: not air or lung


def test_an_empty_body_yields_null_spatial_features_not_a_crash() -> None:
    blob = nifti_bytes(np.full((3, 3, 3), -1000.0, dtype=np.float32), IDENTITY)
    f = compute_features(load(blob, CONTRACT))
    assert f["body_volume_ml"] == 0.0
    assert f["centroid_ras_mm"] is None and f["extent_ras_mm"] is None


def test_features_are_deterministic_and_rounded() -> None:
    blob, _ = _sphere()
    a = json.dumps(compute_features(load(blob, CONTRACT)), sort_keys=True)
    b = json.dumps(compute_features(load(blob, CONTRACT)), sort_keys=True)
    assert a == b
    for v in compute_features(load(blob, CONTRACT))["centroid_ras_mm"]:
        assert round(v, 3) == v


def _windowed_to_the_head_region(name: str) -> np.ndarray:
    """The phantom on grid `name`, with everything outside the common world-space head box set to
    air. Grids are rotated bounding boxes of different extent (the tilted one also reaches ~50 mm
    further down the neck), so without a common window the five arrays are not the same physical
    region -- a fixture property, not a consumer one. World coordinates come from the raw affine,
    one axis at a time (bounded memory)."""
    ph = phantom(name)
    out = ph.volume.copy()
    outside = np.zeros(out.shape, dtype=bool)
    for axis, (lo, hi) in enumerate(hp.HEAD_BBOX):
        w = world_axis(ph.grid, axis)
        outside |= (w < lo - 0.5) | (w > hi + 0.5)
        del w
    out[outside] = hp.AIR
    return out


@pytest.fixture(scope="module")
def head_features() -> dict[str, dict]:  # type: ignore[type-arg]
    out = {}
    for name in sorted(hp.ROTATIONS):
        ph = phantom(name)
        blob = nifti_bytes(_windowed_to_the_head_region(name), ph.grid.affine_ras)
        out[name] = compute_features(load(blob, CONTRACT))
    return out


def test_physical_features_are_identical_across_all_five_orientations(
    head_features: dict[str, dict],  # type: ignore[type-arg]
) -> None:
    """The same physical head rendered on five different grids must give the same physical
    answer -- the property an index-based (orientation-blind) consumer cannot have."""
    base = head_features["ras_native"]
    for name, f in head_features.items():
        assert f["body_volume_ml"] == pytest.approx(base["body_volume_ml"], rel=0.015), name
        assert f["centroid_ras_mm"] == pytest.approx(base["centroid_ras_mm"], abs=1.0), name
        for edge in ("min", "max"):
            assert f["extent_ras_mm"][edge] == pytest.approx(
                base["extent_ras_mm"][edge], abs=2.0
            ), (name, edge)


def test_the_orientation_test_can_actually_fail() -> None:
    """A naive consumer that averages array INDICES gets different answers on different grids."""
    naive = {}
    for name in ("ras_native", "z_reversed", "axis_permuted"):
        ph = phantom(name)
        idx = np.argwhere(ph.volume > -500)
        naive[name] = idx.mean(axis=0)
    assert np.abs(naive["ras_native"] - naive["z_reversed"]).max() > 5.0
    assert np.abs(naive["ras_native"] - naive["axis_permuted"]).max() > 5.0


def test_the_head_phantoms_anterior_extent_is_the_faces_side(
    head_features: dict[str, dict],  # type: ignore[type-arg]
) -> None:
    """Face toward +y (anterior) in the phantom: the RAS extent must reach ~+123 mm on y (nose
    tip) and ~-90 on the posterior side, on every grid."""
    for name, f in head_features.items():
        assert f["extent_ras_mm"]["max"][1] == pytest.approx(123.0, abs=3.0), name
        assert f["extent_ras_mm"]["min"][1] == pytest.approx(-90.0, abs=3.0), name

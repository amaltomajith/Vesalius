"""TESTING.md Proof 4 -- Contract violation: consumers are protected from upstream schema drift.

Claim: a consumer that pins a contract version rejects data produced under a different
(incompatible) contract without any code change.

    def test_consumer_rejects_orientation_change():
        contract = load_contract("volume_contract", "1.1")   # LPS instead of RAS
        with pytest.raises(ContractViolation, match="RAS"):
            cv_consumer.load(volume, contract)

TESTING.md rationale: left-right mirroring from an unnoticed orientation flip is a real and
serious class of medical imaging bug.  A consumer that does not validate the contract claims
will silently operate on a mirrored volume -- results look plausible but are anatomically
inverted.  The contract exists to make the pipeline`s coordinate-frame guarantee machine-
checkable, not just documented.

This proof does NOT require infrastructure (no Postgres, no MinIO): the consumer`s
`load(volume_bytes, contract)` is a pure function.  A valid NIfTI-1 volume is built from
a known array using the same `nifti_bytes` helper that the unit tier already relies on.

Four scenarios are exercised, in escalating specificity:

1. Orientation contract drift (LPS vs RAS) -- the spec`s primary example.
2. Major version bump -- a consumer that pinned "1.0" refuses data under "2.x".
3. Defacing contract violation -- manifest says defacing required but not applied.
4. Array layout drift -- axis_order changed from [z, y, x] to [x, y, z].

Together they demonstrate that *both* the contract document AND the manifest are checked,
and that ANY discrepancy raises ContractViolation, not a warning.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from consumer.contract import ContractViolation, validate_manifest
from consumer.loader import load
from pipeline.catalog.contracts import VOLUME_CONTRACT_V1_0
from tests.fixtures.deface_runs import nifti_bytes

# A minimal but valid volume: 4x5x6 float32 with identity affine.
_ARRAY = np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6) - 100.0
_IDENTITY = np.eye(4)
_CONTRACT_V1_0 = VOLUME_CONTRACT_V1_0


def _valid_volume() -> bytes:
    return nifti_bytes(_ARRAY, _IDENTITY)


def _contract(**overrides: object) -> dict:  # type: ignore[type-arg]
    """Deep-copy V1.0 and apply flat `nested__key` overrides."""
    c = copy.deepcopy(_CONTRACT_V1_0)
    for path, value in overrides.items():
        node = c
        *parents, leaf = path.split("__")
        for p in parents:
            node = node[p]  # type: ignore[assignment]
        node[leaf] = value  # type: ignore[index]
    return c


def _manifest(**overrides: object) -> dict:  # type: ignore[type-arg]
    m: dict = {  # type: ignore[type-arg]
        "contract_version": "1.0",
        "geometry": {"source_frame": "RAS_mm"},
        "deface": {"required": True, "applied": True},
    }
    m.update(overrides)
    return m


# ---------------------------------------------------------------------------
# Proof 4 primary case: orientation change (LPS vs RAS)
# ---------------------------------------------------------------------------


def test_consumer_rejects_orientation_change() -> None:
    """TESTING.md Proof 4: `cv_consumer.load(volume, contract)` raises ContractViolation("RAS").

    A hypothetical v1.1 that changed the coordinate_frame guarantee from RAS to LPS must
    be rejected immediately -- before a single voxel is read for inference.
    """
    lps_contract = _contract(version="1.1", spatial__coordinate_frame="LPS")

    with pytest.raises(ContractViolation, match="RAS"):
        load(_valid_volume(), lps_contract)


def test_consumer_rejects_orientation_change_regardless_of_volume_content() -> None:
    """The contract document is validated FIRST; the volume bytes are irrelevant.

    This is the key architectural point: contract-level protection is independent of what
    the producer actually wrote.  A valid RAS volume passed to an LPS contract is still
    refused -- the consumer has no way to verify the claim without re-computing the
    orientation from scratch, which is exactly what `load` does.
    """
    lps_contract = _contract(version="1.1", spatial__coordinate_frame="LPS")

    # The volume itself IS a valid RAS volume -- the refusal is about the contract claim.
    with pytest.raises(ContractViolation, match="RAS"):
        load(_valid_volume(), lps_contract)


# ---------------------------------------------------------------------------
# Proof 4 secondary cases: other forms of contract drift
# ---------------------------------------------------------------------------


def test_consumer_rejects_major_version_bump() -> None:
    """A consumer that pinned "1.x" refuses a contract claiming major version 2.

    If a hypothetical upstream breaking change shipped under "2.0", consumers would not
    silently accept it.
    """
    v2_contract = _contract(version="2.0")

    with pytest.raises(ContractViolation, match="major version"):
        load(_valid_volume(), v2_contract)


def test_consumer_rejects_axis_order_change() -> None:
    """A contract that claims [x, y, z] axis order is incompatible with [z, y, x].

    An index-based consumer that iterates in a fixed order would silently process the
    wrong axis if this check were absent.
    """
    transposed_contract = _contract(array__axis_order=["x", "y", "z"])

    with pytest.raises(ContractViolation, match="axis order"):
        load(_valid_volume(), transposed_contract)


def test_consumer_rejects_intensity_unit_change() -> None:
    """A contract claiming arbitrary units cannot be loaded by a HU-expecting consumer."""
    arbitrary_contract = _contract(intensity__units="arbitrary")

    with pytest.raises(ContractViolation, match="HU"):
        load(_valid_volume(), arbitrary_contract)


# ---------------------------------------------------------------------------
# Proof 4 manifest cases: the deface guarantee is machine-checkable
# ---------------------------------------------------------------------------


def test_consumer_rejects_defacing_not_applied_when_required() -> None:
    """A manifest that admits defacing was required but not applied is refused.

    This is the precise scenario TESTING.md flags: a head volume served without defacing
    would create a re-identification channel.  The consumer enforces the guarantee, not
    just the producer.
    """
    bad_manifest = _manifest(deface={"required": True, "applied": False})

    with pytest.raises(ContractViolation, match="defacing"):
        load(_valid_volume(), _CONTRACT_V1_0, bad_manifest)


def test_consumer_accepts_defacing_not_required_with_explicit_record() -> None:
    """A non-head series where defacing was not required is accepted -- the key is the
    explicit record (required=False), not the absence of a deface block."""
    good_manifest = _manifest(deface={"required": False, "applied": False})
    # Should not raise.
    load(_valid_volume(), _CONTRACT_V1_0, good_manifest)


def test_consumer_rejects_manifest_from_a_different_contract_version() -> None:
    """A manifest produced under a different contract version is refused even if the
    volume itself is valid -- a consumer cannot know what guarantees the producer made
    if the versions are mismatched."""
    mismatched_manifest = _manifest(contract_version="1.1")

    with pytest.raises(ContractViolation, match="contract"):
        validate_manifest(_CONTRACT_V1_0, mismatched_manifest)


def test_consumer_rejects_manifest_recording_lps_source_frame() -> None:
    """A manifest whose geometry.source_frame says LPS contradicts the RAS guarantee."""
    lps_manifest = _manifest(geometry={"source_frame": "LPS_mm"})

    with pytest.raises(ContractViolation, match="RAS"):
        validate_manifest(_CONTRACT_V1_0, lps_manifest)


# ---------------------------------------------------------------------------
# The structural invariant: ContractViolation is not a soft warning
# ---------------------------------------------------------------------------


def test_contract_violation_is_an_exception_not_a_warning() -> None:
    """ContractViolation must be raised, not returned or logged.

    A consumer that swallows a ContractViolation and continues provides no protection.
    This test confirms that `load` raises (i.e. the violation reaches the caller`s
    exception handler), not that it merely logs a warning or returns a sentinel.
    """
    lps_contract = _contract(version="1.1", spatial__coordinate_frame="LPS")

    try:
        load(_valid_volume(), lps_contract)
        raise AssertionError(
            "load did not raise -- ContractViolation must be raised, not swallowed"
        )
    except ContractViolation:
        pass  # correct: the violation propagates


def test_valid_v1_volume_is_accepted() -> None:
    """Baseline: a well-formed volume against the canonical v1.0 contract loads without error."""
    v = load(_valid_volume(), _CONTRACT_V1_0)
    assert v.array.shape == (4, 5, 6)
    assert v.array.dtype == np.float32
    assert np.array_equal(v.array, _ARRAY)

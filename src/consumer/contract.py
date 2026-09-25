"""Validation of what the consumer is handed against the contract it was promised.

Every check raises `ContractViolation`. A refusal is the point: a mismatch must stop the
consumer, never be "best-effort" tolerated (a silently mirrored volume looks exactly like a
correct one).
"""

from __future__ import annotations

from typing import Any

SUPPORTED_MAJOR = 1
SUPPORTED_ARTIFACT_KIND = "volume_nifti"
SUPPORTED_FORMAT = "nifti-1-gzip"
SUPPORTED_AXIS_ORDER = ["z", "y", "x"]
SUPPORTED_FRAME = "RAS"
SUPPORTED_UNITS = "hounsfield_units"


class ContractViolation(Exception):  # noqa: N818 -- the name DATA-MODEL.md/TESTING.md mandate
    """The volume, manifest or contract does not satisfy what this consumer supports."""


class IntegrityError(Exception):
    """Downloaded bytes differ from what the catalog recorded (never 'close enough')."""


def validate_contract(contract: dict[str, Any]) -> None:
    """The contract's own claims -- what this consumer can and cannot read."""
    try:
        major = str(contract["version"]).split(".")[0]
    except KeyError as exc:
        raise ContractViolation("contract has no version") from exc
    if major != str(SUPPORTED_MAJOR):
        raise ContractViolation(
            f"major version {contract['version']} unsupported (supported: {SUPPORTED_MAJOR}.x)"
        )
    if contract.get("artifact_kind") != SUPPORTED_ARTIFACT_KIND:
        raise ContractViolation(f"unsupported artifact_kind {contract.get('artifact_kind')!r}")
    if contract.get("format") != SUPPORTED_FORMAT:
        raise ContractViolation(f"unsupported format {contract.get('format')!r}")
    frame = contract.get("spatial", {}).get("coordinate_frame")
    if frame != SUPPORTED_FRAME:
        raise ContractViolation(f"expected RAS orientation, contract declares {frame!r}")
    units = contract.get("intensity", {}).get("units")
    if units != SUPPORTED_UNITS:
        raise ContractViolation(f"expected HU intensity, contract declares {units!r}")
    array = contract.get("array", {})
    if array.get("axis_order") != SUPPORTED_AXIS_ORDER:
        raise ContractViolation(
            f"expected axis order z,y,x, contract declares {array.get('axis_order')!r}"
        )
    if array.get("dimensions") != 3:
        raise ContractViolation(
            f"expected 3 dimensions, contract declares {array.get('dimensions')!r}"
        )


def validate_manifest(contract: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Cross-checks the gold manifest against the contract and the defacing guarantee."""
    if manifest.get("contract_version") != contract.get("version"):
        raise ContractViolation(
            f"manifest was produced under contract {manifest.get('contract_version')!r}, "
            f"not {contract.get('version')!r}"
        )
    source = manifest.get("geometry", {}).get("source_frame")
    if source != "RAS_mm":
        raise ContractViolation(f"expected RAS orientation, manifest records {source!r}")
    deface = manifest.get("deface", {})
    if deface.get("required") and not deface.get("applied"):
        raise ContractViolation("defacing was required but the manifest says it was not applied")

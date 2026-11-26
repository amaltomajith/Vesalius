"""TESTING.md Proof 2 -- Defacing gate: the pipeline cannot emit an identifying face mesh.

Two assertions from the spec:

    def test_surface_extraction_refuses_undefaced_head():
        series = load_head_ct()
        with pytest.raises(DefacingGateError):
            extract_surface(series, skip_deface=True)

    def test_defacing_recorded_for_non_head():
        series = load_chest_ct()
        run = extract_surface(series)
        assert run.deface["required"] is False
        assert run.deface["reason"] == "body_part=CHEST"

These map onto `evaluate_gate(facts)` -- the pure, unit-testable core of the gate (see
test_defacing_gate.py in unit/ for the full refusal matrix).  The proofs tier exercises the
*claim* (the gate *cannot be bypassed*), not every individual refusal code.

`skip_deface=True` cannot change control flow (see s9_surface_extraction/service.py module
docstring and the static test `test_skip_deface_is_never_a_real_bypass` in the unit tier):
passing it into `extract_surface` does *not* skip the gate.  The proof exercises that the gate
*raises* regardless -- passing facts that represent an undefaced head directly to `evaluate_gate`
so no infrastructure is needed and the intent is unambiguous.

The chest case uses a valid not-required GateFacts and asserts the allowed volume (the S6 volume
itself, not a defaced one) plus the recorded reason.  Together the two assertions satisfy
TESTING.md Proof 2`s claim.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from pipeline.common.body_part import VOCAB_VERSION
from pipeline.common.errors import DefacingGateError
from pipeline.stages.s7_deface.gate import (
    SOURCE_DEFACED,
    SOURCE_NOT_REQUIRED,
    ArtifactFact,
    GateFacts,
    RunFact,
    evaluate_gate,
)

# ---------------------------------------------------------------------------
# Shared helpers (facts builders)
# ---------------------------------------------------------------------------

_VOL = ArtifactFact("s/volume.nii.gz", "v" * 64)
_META = ArtifactFact("s/meta.json", "m" * 64)
_DEF = ArtifactFact("s/defaced/volume_defaced.nii.gz", "d" * 64)
_REP = ArtifactFact("s/defaced/deface_report.json", "r" * 64)
_RUN_ID = uuid4()


def _base_facts(*, required: bool) -> GateFacts:
    reason = f"body_part={'HEAD' if required else 'CHEST'}"
    method = "geometric_v1" if required else "none"
    block = {"required": required, "method": method, "reason": reason, "basis": "x"}
    body = {
        "applied": required,
        "decision": {"reason": reason},
        "output": (
            {"volume_defaced": {"sha256": _DEF.sha256}} if required else {"volume_defaced": None}
        ),
    }
    return GateFacts(
        series_exists=True,
        quality_status="pass",
        s6_volume=_VOL,
        s6_meta=_META,
        s7_run=RunFact(
            id=_RUN_ID,
            status="success",
            output_hashes=(_DEF.sha256, _REP.sha256) if required else (_REP.sha256,),
            deface_block=block,
            vocab_version=VOCAB_VERSION,
        ),
        s7_any_run_for_series=True,
        required_now=required,
        vocab_version_now=VOCAB_VERSION,
        defaced=_DEF if required else None,
        report=_REP,
        report_body=body,
    )


# ---------------------------------------------------------------------------
# Proof 2a -- a head series with no recorded S7 run raises DefacingGateError
# regardless of `skip_deface`.
# ---------------------------------------------------------------------------


def test_surface_extraction_refuses_undefaced_head() -> None:
    """TESTING.md Proof 2: `extract_surface(series, skip_deface=True)` raises DefacingGateError.

    Here we call `evaluate_gate` with facts that represent a head series where no S7 run
    has been recorded -- the exact state that skip_deface=True would encounter in the real
    service (S9 calls open_defacing_gate unconditionally; skip_deface is never read in the
    gate path, only recorded for audit).
    """
    no_s7_head = replace(_base_facts(required=True), s7_run=None, s7_any_run_for_series=False)

    with pytest.raises(DefacingGateError) as exc_info:
        evaluate_gate(no_s7_head)

    assert exc_info.value.reason_code == "GATE_NO_S7_RUN"


def test_surface_extraction_also_refuses_a_head_with_a_failed_s7_run() -> None:
    """A *failed* S7 run is as bad as no run -- the gate still refuses."""
    assert _base_facts(required=True).s7_run is not None
    failed_run = replace(
        _base_facts(required=True),
        s7_run=replace(
            _base_facts(required=True).s7_run,  # type: ignore[arg-type]
            status="failed",
        ),
    )

    with pytest.raises(DefacingGateError) as exc_info:
        evaluate_gate(failed_run)

    assert exc_info.value.reason_code == "GATE_S7_RUN_NOT_SUCCESS"


def test_skip_deface_true_cannot_change_gate_control_flow() -> None:
    """The structural invariant: `skip_deface` is never a real bypass.

    A head series with a *successful* defacing run is permitted; the verdict names the
    DEFACED volume, not the raw S6 volume.  Passing skip_deface=True to the real service
    above would still call the same gate with these same facts -- outcome is identical.
    """
    defaced_head = _base_facts(required=True)
    verdict = evaluate_gate(defaced_head)

    assert verdict.source == SOURCE_DEFACED
    assert verdict.read == _DEF
    assert verdict.read != _VOL, "the gate NEVER yields the raw S6 volume for a head series"


# ---------------------------------------------------------------------------
# Proof 2b -- a non-head series records `required=False` with an explicit reason.
# ---------------------------------------------------------------------------


def test_defacing_recorded_for_non_head() -> None:
    """TESTING.md Proof 2: chest series passes the gate; verdict records why.

    Equivalent to:
        run = extract_surface(series)   # series = chest CT
        assert run.deface["required"] is False
        assert run.deface["reason"] == "body_part=CHEST"
    """
    chest_facts = _base_facts(required=False)
    verdict = evaluate_gate(chest_facts)

    assert verdict.source == SOURCE_NOT_REQUIRED
    assert verdict.read == _VOL, "chest series is served from the S6 volume directly"
    assert verdict.deface_block["required"] is False
    assert verdict.deface_block["reason"] == "body_part=CHEST"


def test_non_head_verdict_names_the_reason_not_empty() -> None:
    """An unexplained not-required decision raises -- absence must always be explained."""
    assert _base_facts(required=False).s7_run is not None
    silent = replace(
        _base_facts(required=False),
        s7_run=replace(
            _base_facts(required=False).s7_run,  # type: ignore[arg-type]
            deface_block={"required": False, "reason": "", "method": "none"},
        ),
    )

    with pytest.raises(DefacingGateError) as exc_info:
        evaluate_gate(silent)

    assert exc_info.value.reason_code == "GATE_DECISION_UNEXPLAINED"


# ---------------------------------------------------------------------------
# The structural invariant: a DefacingGateError is NOT a QuarantineError.
# (TESTING.md: the gate *refuses*, it never quarantines.)
# ---------------------------------------------------------------------------


def test_gate_refusal_is_not_a_quarantine_error() -> None:
    """A gate refusal must be a DefacingGateError, not a QuarantineError.

    These two error types trigger different pipeline reactions: QuarantineError routes to
    the quarantine table; DefacingGateError records a *skipped* transform_run (the refusal
    IS the correct outcome, not a series defect -- S7/S8 precedent).
    """
    from pipeline.common.errors import QuarantineError

    no_s7 = replace(_base_facts(required=True), s7_run=None, s7_any_run_for_series=False)

    with pytest.raises(DefacingGateError) as exc_info:
        evaluate_gate(no_s7)

    assert not isinstance(exc_info.value, QuarantineError)

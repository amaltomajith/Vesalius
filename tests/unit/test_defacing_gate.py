"""The defacing gate. `evaluate_gate` is pure, so every refusal is exercised here without
infrastructure; `open_defacing_gate` (DB + MinIO) is covered by the integration tier.

A gate that cannot refuse proves nothing, so the matrix is a table of single-fact breakages of
an otherwise valid facts object, each with the exact refusal code expected.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from pipeline.common.body_part import VOCAB_VERSION
from pipeline.common.errors import DefacingGateError
from pipeline.stages.s7_deface.gate import (
    SOURCE_DEFACED,
    SOURCE_NOT_REQUIRED,
    ArtifactFact,
    GatedVolume,
    GateFacts,
    RunFact,
    evaluate_gate,
)

VOL = ArtifactFact("s/volume.nii.gz", "v" * 64)
META = ArtifactFact("s/meta.json", "m" * 64)
DEF = ArtifactFact("s/defaced/k/volume_defaced.nii.gz", "d" * 64)
REP = ArtifactFact("s/defaced/k/deface_report.json", "r" * 64)
RUN_ID = uuid4()


def _facts(required: bool, **over: Any) -> GateFacts:
    reason = "body_part=HEAD" if required else "body_part=CHEST"
    block = {
        "required": required,
        "method": "geometric_v1" if required else "none",
        "reason": reason,
        "basis": "x",
    }
    body = {
        "applied": required,
        "decision": {"reason": reason},
        "output": {"volume_defaced": {"sha256": DEF.sha256} if required else None},
    }
    facts = GateFacts(
        series_exists=True,
        quality_status="pass",
        s6_volume=VOL,
        s6_meta=META,
        s7_run=RunFact(
            id=RUN_ID,
            status="success",
            output_hashes=(DEF.sha256, REP.sha256) if required else (REP.sha256,),
            deface_block=block,
            vocab_version=VOCAB_VERSION,
        ),
        s7_any_run_for_series=True,
        required_now=required,
        vocab_version_now=VOCAB_VERSION,
        defaced=DEF if required else None,
        report=REP,
        report_body=body,
    )
    return replace(facts, **over)


def _run(facts: GateFacts, **over: Any) -> GateFacts:
    assert facts.s7_run is not None
    return replace(facts, s7_run=replace(facts.s7_run, **over))


def _refusal(facts: GateFacts) -> str:
    with pytest.raises(DefacingGateError) as exc:
        evaluate_gate(facts)
    return exc.value.reason_code


def test_a_valid_required_case_permits_only_the_defaced_volume() -> None:
    v = evaluate_gate(_facts(True))
    assert v.source == SOURCE_DEFACED
    assert v.read == DEF and v.read != VOL
    assert v.run_id == RUN_ID


def test_a_valid_not_required_case_permits_the_s6_volume_with_its_reason() -> None:
    v = evaluate_gate(_facts(False))
    assert v.source == SOURCE_NOT_REQUIRED
    assert v.read == VOL
    assert v.deface_block["reason"] == "body_part=CHEST"


REFUSALS: list[tuple[str, Any, str]] = [
    ("no such series", lambda: _facts(True, series_exists=False), "GATE_SERIES_NOT_FOUND"),
    (
        "series not passed",
        lambda: _facts(True, quality_status="quarantined"),
        "GATE_SERIES_NOT_PASSED",
    ),
    ("no s6 volume", lambda: _facts(True, s6_volume=None), "GATE_NO_S6_VOLUME"),
    ("no s6 meta", lambda: _facts(True, s6_meta=None), "GATE_NO_S6_VOLUME"),
    (
        "non-head series with no S7 run at all",
        lambda: _facts(False, s7_run=None, s7_any_run_for_series=False),
        "GATE_NO_S7_RUN",
    ),
    (
        "S7 ran only against an older S6 volume",
        lambda: _facts(True, s7_run=None, s7_any_run_for_series=True),
        "GATE_S7_RUN_STALE",
    ),
    ("run failed", lambda: _run(_facts(True), status="failed"), "GATE_S7_RUN_NOT_SUCCESS"),
    ("run skipped", lambda: _run(_facts(True), status="skipped"), "GATE_S7_RUN_NOT_SUCCESS"),
    ("run still running", lambda: _run(_facts(True), status="running"), "GATE_S7_RUN_NOT_SUCCESS"),
    (
        "not required but no reason",
        lambda: _run(
            _facts(False), deface_block={"required": False, "reason": "", "method": "none"}
        ),
        "GATE_DECISION_UNEXPLAINED",
    ),
    (
        "not required with whitespace reason",
        lambda: _run(
            _facts(False), deface_block={"required": False, "reason": "  ", "method": "none"}
        ),
        "GATE_DECISION_UNEXPLAINED",
    ),
    (
        "reason missing entirely",
        lambda: _run(_facts(False), deface_block={"required": False, "method": "none"}),
        "GATE_DECISION_UNEXPLAINED",
    ),
    (
        "required flag not a bool",
        lambda: _run(
            _facts(False), deface_block={"required": "no", "reason": "x", "method": "none"}
        ),
        "GATE_DECISION_UNEXPLAINED",
    ),
    (
        "vocabulary changed since",
        lambda: _run(_facts(False), vocab_version="0"),
        "GATE_VOCAB_STALE",
    ),
    (
        "vocabulary version unrecorded",
        lambda: _run(_facts(True), vocab_version=None),
        "GATE_VOCAB_STALE",
    ),
    (
        "flagged not-required but required under the current catalog",
        lambda: _facts(False, required_now=True),
        "GATE_DECISION_STALE",
    ),
    (
        "cannot recompute the decision",
        lambda: _facts(False, required_now=None),
        "GATE_DECISION_UNVERIFIABLE",
    ),
    ("no report artifact", lambda: _facts(True, report=None), "GATE_REPORT_MISSING"),
    (
        "report not among the run's outputs",
        lambda: _facts(True, report=ArtifactFact("x", "z" * 64)),
        "GATE_REPORT_MISSING",
    ),
    (
        "report says defacing was not applied",
        lambda: _facts(
            True, report_body={"applied": False, "decision": {"reason": "body_part=HEAD"}}
        ),
        "GATE_REPORT_DISAGREES",
    ),
    (
        "report reason differs from the run's",
        lambda: _facts(
            False, report_body={"applied": False, "decision": {"reason": "body_part=ABDOMEN"}}
        ),
        "GATE_REPORT_DISAGREES",
    ),
    ("no report body", lambda: _facts(False, report_body=None), "GATE_REPORT_DISAGREES"),
    (
        "defaced artifact missing",
        lambda: _facts(True, defaced=None),
        "GATE_DEFACED_ARTIFACT_MISSING",
    ),
    (
        "defaced artifact not among run outputs",
        lambda: _facts(True, defaced=ArtifactFact("x", "z" * 64)),
        "GATE_DEFACED_NOT_IN_RUN_OUTPUTS",
    ),
    (
        "report names a different defaced hash",
        lambda: _facts(
            True,
            report_body={
                "applied": True,
                "decision": {"reason": "body_part=HEAD"},
                "output": {"volume_defaced": {"sha256": "z" * 64}},
            },
        ),
        "GATE_REPORT_DISAGREES",
    ),
]


@pytest.mark.parametrize(
    "build, code", [(b, c) for _, b, c in REFUSALS], ids=[n for n, _, _ in REFUSALS]
)
def test_the_gate_refuses_with_the_specific_code(build: Any, code: str) -> None:
    assert _refusal(build()) == code


def test_defacing_needlessly_is_accepted() -> None:
    """The reverse of a stale not-required: defaced although the catalog now says not required."""
    v = evaluate_gate(_facts(True, required_now=False))
    assert v.source == SOURCE_DEFACED


def test_a_required_decision_never_yields_the_s6_volume() -> None:
    for facts in (_facts(True), _facts(True, required_now=False)):
        assert evaluate_gate(facts).read != VOL


def test_a_refusal_is_not_a_quarantine_error() -> None:
    from pipeline.common.errors import QuarantineError

    with pytest.raises(DefacingGateError) as exc:
        evaluate_gate(_facts(True, quality_status="x"))
    assert not isinstance(exc.value, QuarantineError)


def _forge(**over: Any) -> GatedVolume:
    kw: dict[str, Any] = dict(
        series_id=uuid4(),
        volume_bytes=b"v",
        volume_sha256="s",
        volume_key="k",
        meta_bytes=b"m",
        meta_sha256="m",
        s7_run_id=uuid4(),
        source=SOURCE_NOT_REQUIRED,
        _block={"required": False, "method": "none", "reason": "body_part=CHEST"},
    )
    kw.update(over)
    return GatedVolume(**kw)


def test_a_gated_volume_cannot_be_constructed_outside_the_gate() -> None:
    with pytest.raises(DefacingGateError) as exc:
        _forge()
    assert exc.value.reason_code == "GATE_FORGED"
    with pytest.raises(DefacingGateError):
        _forge(_seal=object())
    with pytest.raises(DefacingGateError):
        _forge(_seal=None)


def test_the_issued_volume_reports_a_manifest_block_with_applied_and_reason() -> None:
    from pipeline.stages.s7_deface import gate

    v = _forge(_seal=gate._TOKEN)
    assert v.deface_block() == {
        "required": False,
        "applied": False,
        "method": "none",
        "reason": "body_part=CHEST",
    }
    d = _forge(
        _seal=gate._TOKEN,
        source=SOURCE_DEFACED,
        _block={"required": True, "method": "geometric_v1", "reason": "body_part=HEAD"},
    )
    assert d.deface_block()["applied"] is True
    with pytest.raises(Exception):  # noqa: B017 -- frozen dataclass: FrozenInstanceError
        d.volume_bytes = b"other"  # type: ignore[misc]

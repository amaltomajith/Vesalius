"""The interface's plain-language layer must cover EVERYTHING the system can say.

The reason codes are collected by scanning the source tree, not from a hand-kept list, so adding a
new `QG_*` / `S<n>_*` / `GATE_*` code (or a stage skip reason) without an explanation fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipeline.demo.explain import GLOSSARY, STEPS, explain

SRC = Path(__file__).parents[2] / "src" / "pipeline"
CODE_RE = re.compile(r"\"((?:QG|GATE|S\d+)_[A-Z0-9_]+)\"")
# stages record a skip either as `_skip("reason", ...)` or as `reason = "reason"`
SKIP_RE = re.compile(
    r"(?:_skip\(\s*|(?<![a-z_])reason = )\"("
    r"series_[a-z0-9_]+|defacing_gate_refused|duplicate_content)\""
)
DEMO_DIR = SRC / "demo"


def _emitted_codes() -> set[str]:
    codes: set[str] = set()
    for path in SRC.rglob("*.py"):
        if DEMO_DIR in path.parents:
            continue  # the glossary itself
        codes.update(CODE_RE.findall(path.read_text(encoding="utf-8")))
    return codes


def _emitted_skip_reasons() -> set[str]:
    reasons: set[str] = set()
    # every stage module, not only files named service.py: structures mode lives in
    # structures_service.py / structures_export.py and its skip reasons must be covered too
    for path in (SRC / "stages").rglob("*.py"):
        reasons.update(SKIP_RE.findall(path.read_text(encoding="utf-8")))
    return reasons


def test_the_scan_finds_a_realistic_number_of_codes() -> None:
    """Guards the guard: if the regex silently matched nothing this file would prove nothing."""
    assert len(_emitted_codes()) >= 60
    assert {"defacing_gate_refused", "series_has_no_s10_mesh"} <= _emitted_skip_reasons()


@pytest.mark.parametrize("code", sorted(_emitted_codes()))
def test_every_reason_code_the_system_can_emit_has_a_plain_explanation(code: str) -> None:
    assert code in GLOSSARY, f"{code} is emitted by the pipeline but has no plain-language text"


@pytest.mark.parametrize("reason", sorted(_emitted_skip_reasons()))
def test_every_skip_reason_has_a_plain_explanation(reason: str) -> None:
    assert reason in GLOSSARY, reason


def test_no_glossary_entry_is_stale() -> None:
    """Every entry describes something the system can still say (a removed code should be removed
    from the glossary too)."""
    known = (
        _emitted_codes() | _emitted_skip_reasons() | {"QG_DUPLICATE_CONTENT", "duplicate_content"}
    )
    assert set(GLOSSARY) - known == set()


@pytest.mark.parametrize("text", list(GLOSSARY.values()))
def test_explanations_are_sentences_not_codes(text: str) -> None:
    assert text.endswith((".", ")")) and " " in text
    assert not re.search(r"\b(?:QG|GATE|S\d+)_[A-Z]", text)  # never repeats a raw code


def test_explain_composes_a_skip_and_its_gate_code() -> None:
    text = explain("defacing_gate_refused:GATE_NO_S7_RUN")
    assert "safety gate" in text and "face-removal step has not been run" in text


def test_explain_never_raises_and_never_returns_a_bare_code() -> None:
    for weird in (None, "", "TOTALLY_NEW_CODE", "some free text with spaces!", "S99_X"):
        out = explain(weird)
        assert out and out != weird and " " in out


def test_the_ten_steps_cover_every_pipeline_stage_once_in_order() -> None:
    stages = [s for step in STEPS for s in step.stages]
    assert stages == [f"S{i}" for i in range(1, 12)]
    assert len({s.key for s in STEPS}) == len(STEPS) == 10

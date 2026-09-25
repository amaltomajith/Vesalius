"""S7 and S8 must threshold on the same numbers, and those numbers must be GEOMETRY.md's."""

from __future__ import annotations

import pathlib
import re

from pipeline.common.hu_presets import PRESETS_V1
from pipeline.stages.s7_deface import params

_REFS = pathlib.Path(__file__).resolve().parents[2] / "refs" / "GEOMETRY.md"


def _presets_from_ref() -> dict[str, dict[str, int]]:
    text = _REFS.read_text(encoding="utf-8")
    block = text[text.index("PRESETS_V1 = {") :]
    block = block[: block.index("}\n```")]
    found = re.findall(r'"(\w+)":\s*\{"min":\s*(-?\d+),\s*"max":\s*(-?\d+)\}', block)
    return {name: {"min": int(lo), "max": int(hi)} for name, lo, hi in found}


def test_presets_are_verbatim_from_geometry_md() -> None:
    from_ref = _presets_from_ref()
    assert len(from_ref) == 6  # guards the parser itself
    assert from_ref == PRESETS_V1


def test_s7_body_threshold_is_the_skin_surface_preset_not_a_private_literal() -> None:
    assert params.TISSUE_MIN_HU == PRESETS_V1["skin_surface"]["min"] == -200.0


def test_bone_floor_is_below_the_bone_preset_so_partial_volume_cortex_stops_the_strip() -> None:
    assert PRESETS_V1["bone"]["min"] > params.BONE_FLOOR_HU

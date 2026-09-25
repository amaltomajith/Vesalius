"""The published `volume_contract` v1.0 must agree with what S6 actually writes into meta.json --
otherwise the contract a consumer pins is fiction. Also: the contract is not mutable by accident.
"""

from __future__ import annotations

import copy

import numpy as np
import pytest

from pipeline.catalog.contracts import (
    PUBLISHED_CONTRACTS,
    VOLUME_CONTRACT_V1_0,
    ContractConflictError,
    _canonical,
    publish_contract,
)
from pipeline.stages.s6_volume_assembly.meta import CONTRACT_NAME, CONTRACT_VERSION
from tests.fixtures.deface_runs import make_s6_stub


def _meta() -> dict:  # type: ignore[type-arg]
    return make_s6_stub(b"x", (2, 2, 2), np.eye(4)).meta


def test_the_contract_name_and_version_are_the_ones_s6_stamps_on_every_volume() -> None:
    assert VOLUME_CONTRACT_V1_0["name"] == CONTRACT_NAME
    assert VOLUME_CONTRACT_V1_0["version"] == CONTRACT_VERSION


def test_the_contract_agrees_with_the_meta_json_s6_writes() -> None:
    c, m = VOLUME_CONTRACT_V1_0, _meta()
    assert c["spatial"]["coordinate_frame"] == m["spatial"]["coordinate_frame"] == "RAS"
    assert c["spatial"]["isotropic"] is m["spatial"]["isotropic"] is True
    assert c["array"]["axis_order"] == m["array"]["axis_order"]
    assert c["array"]["dtype"] == m["array"]["dtype"]
    for k in ("air_value", "water_value", "rescale_applied"):
        assert c["intensity"][k] == m["intensity"][k], k
    assert c["intensity"]["units"] == m["intensity"]["units"]
    assert c["intensity"]["applies_to_modality"] == m["intensity"]["applies_to_modality"]
    assert c["nullability"]["padding_value"] == m["nullability"]["padding_value"]
    assert c["guarantees"][0] == m["guarantees"][0]  # the PS3.15 statement, verbatim


def test_every_published_contract_has_a_unique_name_and_version() -> None:
    keys = [(d["name"], d["version"]) for d in PUBLISHED_CONTRACTS]
    assert len(keys) == len(set(keys))


def test_canonical_form_ignores_key_order_but_not_content() -> None:
    a = copy.deepcopy(VOLUME_CONTRACT_V1_0)
    b = dict(reversed(list(a.items())))
    assert _canonical(a) == _canonical(b)
    b["spatial"] = {**b["spatial"], "coordinate_frame": "LPS"}
    assert _canonical(a) != _canonical(b)


class _Row:
    def __init__(self, schema: dict) -> None:  # type: ignore[type-arg]
        self.schema = schema


class _Result:
    def __init__(self, row: object) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object:
        return self._row


class _Session:
    def __init__(self, existing: object = None) -> None:
        self.existing, self.added = existing, []

    def execute(self, _stmt: object) -> _Result:
        return _Result(self.existing)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        pass


def test_publishing_new_content_inserts_and_identical_republish_is_a_noop() -> None:
    fresh = _Session()
    assert publish_contract(fresh, VOLUME_CONTRACT_V1_0) is True  # type: ignore[arg-type]
    assert len(fresh.added) == 1
    again = _Session(existing=_Row(copy.deepcopy(VOLUME_CONTRACT_V1_0)))
    assert publish_contract(again, VOLUME_CONTRACT_V1_0) is False  # type: ignore[arg-type]
    assert again.added == []


def test_republishing_a_version_with_different_content_is_refused() -> None:
    changed = copy.deepcopy(VOLUME_CONTRACT_V1_0)
    changed["spatial"]["coordinate_frame"] = "LPS"
    with pytest.raises(ContractConflictError):
        publish_contract(_Session(existing=_Row(changed)), VOLUME_CONTRACT_V1_0)  # type: ignore[arg-type]

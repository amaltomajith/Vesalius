"""_get_or_create_uid_map / _get_or_create_pseudo_patient_id only ever call
session.get()/add()/flush() (no select()), so a minimal in-memory fake session is enough
to test their upsert-idempotency without a live Postgres.
"""

from __future__ import annotations

from pipeline.catalog.models import PatientMap, UidMap
from pipeline.stages.s2_parse_index.service import (
    _get_or_create_pseudo_patient_id,
    _get_or_create_uid_map,
)


class FakeSession:
    def __init__(self) -> None:
        self._by_pk: dict[tuple[type, object], object] = {}

    def get(self, model: type, pk: object) -> object | None:
        return self._by_pk.get((model, pk))

    def add(self, obj: object) -> None:
        if isinstance(obj, UidMap):
            self._by_pk[(UidMap, obj.original_uid)] = obj
        elif isinstance(obj, PatientMap):
            self._by_pk[(PatientMap, obj.original_patient_id)] = obj

    def flush(self) -> None:
        pass


def test_new_original_uid_gets_a_provisional_pseudo_uid() -> None:
    session = FakeSession()
    pseudo = _get_or_create_uid_map(session, "1.2.840.study.1", "study")
    assert pseudo != "1.2.840.study.1"
    assert pseudo.startswith("provisional.")


def test_repeated_calls_for_same_original_uid_reuse_the_same_pseudo() -> None:
    session = FakeSession()
    first = _get_or_create_uid_map(session, "1.2.840.study.1", "study")
    second = _get_or_create_uid_map(session, "1.2.840.study.1", "study")
    assert first == second


def test_no_duplicate_rows_created_for_repeated_upsert() -> None:
    session = FakeSession()
    _get_or_create_uid_map(session, "1.2.840.study.1", "study")
    _get_or_create_uid_map(session, "1.2.840.study.1", "study")
    assert len(session._by_pk) == 1


def test_different_original_uids_get_different_pseudo_uids() -> None:
    session = FakeSession()
    pseudo_a = _get_or_create_uid_map(session, "1.2.840.series.1", "series")
    pseudo_b = _get_or_create_uid_map(session, "1.2.840.series.2", "series")
    assert pseudo_a != pseudo_b


def test_pseudo_patient_id_reused_for_same_original_patient_id() -> None:
    session = FakeSession()
    first = _get_or_create_pseudo_patient_id(session, "PAT001")
    second = _get_or_create_pseudo_patient_id(session, "PAT001")
    assert first == second


def test_pseudo_patient_id_with_no_original_id_never_persisted() -> None:
    session = FakeSession()
    pseudo = _get_or_create_pseudo_patient_id(session, None)
    assert pseudo.startswith("provisional.")
    assert len(session._by_pk) == 0

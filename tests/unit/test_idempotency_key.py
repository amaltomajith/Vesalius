from __future__ import annotations

from uuid import uuid4

from pipeline.catalog.idempotency import StageContext, idempotency_key


def _ctx(**overrides: object) -> StageContext:
    defaults: dict[str, object] = {
        "stage": "S1",
        "stage_version": "1.0.0",
        "subject_type": "instance",
        "subject_id": uuid4(),
        "input_hashes": ["hash-a", "hash-b"],
        "params": {"ingest_batch_id": "batch-1"},
    }
    defaults.update(overrides)
    return StageContext(**defaults)  # type: ignore[arg-type]


def test_key_stable_for_identical_context() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id)
    ctx2 = _ctx(subject_id=subject_id)
    assert idempotency_key(ctx1) == idempotency_key(ctx2)


def test_key_independent_of_input_hash_order() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id, input_hashes=["a", "b", "c"])
    ctx2 = _ctx(subject_id=subject_id, input_hashes=["c", "a", "b"])
    assert idempotency_key(ctx1) == idempotency_key(ctx2)


def test_key_independent_of_params_dict_construction_order() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id, params={"a": 1, "b": 2})
    ctx2 = _ctx(subject_id=subject_id, params={"b": 2, "a": 1})
    assert idempotency_key(ctx1) == idempotency_key(ctx2)


def test_key_changes_when_stage_version_changes() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id, stage_version="1.0.0")
    ctx2 = _ctx(subject_id=subject_id, stage_version="1.0.1")
    assert idempotency_key(ctx1) != idempotency_key(ctx2)


def test_key_changes_when_input_hashes_change() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id, input_hashes=["a"])
    ctx2 = _ctx(subject_id=subject_id, input_hashes=["a", "b"])
    assert idempotency_key(ctx1) != idempotency_key(ctx2)


def test_key_changes_when_params_change() -> None:
    subject_id = uuid4()
    ctx1 = _ctx(subject_id=subject_id, params={"ingest_batch_id": "batch-1"})
    ctx2 = _ctx(subject_id=subject_id, params={"ingest_batch_id": "batch-2"})
    assert idempotency_key(ctx1) != idempotency_key(ctx2)


def test_key_independent_of_subject_id() -> None:
    # subject_id is not part of the key payload -- two different subjects with identical
    # stage/version/input_hashes/params legitimately collide (they wouldn't in practice,
    # since input_hashes typically differ per subject).
    ctx1 = _ctx(subject_id=uuid4(), input_hashes=["shared"])
    ctx2 = _ctx(subject_id=uuid4(), input_hashes=["shared"])
    assert idempotency_key(ctx1) == idempotency_key(ctx2)

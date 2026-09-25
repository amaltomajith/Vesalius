from __future__ import annotations

from pipeline.catalog.provisional import provisional_pseudo_uid


def test_provisional_pseudo_uid_is_unique_per_call() -> None:
    values = {provisional_pseudo_uid() for _ in range(50)}
    assert len(values) == 50


def test_provisional_pseudo_uid_never_equals_a_real_looking_original_uid() -> None:
    original_uid = "1.2.840.10008.5.1.4.1.1.2.99999"
    pseudo = provisional_pseudo_uid()
    assert pseudo != original_uid


def test_provisional_pseudo_uid_has_recognizable_marker_prefix() -> None:
    # A distinct, greppable prefix so anything downstream can recognize a value that
    # was never overwritten by S4's real HMAC-derived scheme.
    assert provisional_pseudo_uid().startswith("provisional.")

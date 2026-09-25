"""Proves the "nothing from restricted reaches logs" rule (CLAUDE.md) is enforced, not
just aspirational.
"""

from __future__ import annotations

import io
import logging

from pipeline.common.logging import REDACTED_MARKER, RestrictedFieldFilter, RestrictedValue


def _logger_with_captured_stream(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RestrictedFieldFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger, stream


def test_fstring_interpolation_of_restricted_value_never_carries_the_real_value() -> None:
    original_uid = RestrictedValue("1.2.840.113619.2.55.original-patient-uid")
    message = f"attempting uid_map lookup for uid={original_uid}"
    assert "1.2.840.113619" not in message
    assert REDACTED_MARKER in message


def test_lazy_percent_arg_is_redacted_by_the_filter_before_output() -> None:
    logger, stream = _logger_with_captured_stream("test.restricted.lazy")
    original_patient_id = RestrictedValue("PATIENT-REAL-000123")

    logger.info("processing patient %s", original_patient_id)

    output = stream.getvalue()
    assert "PATIENT-REAL-000123" not in output
    assert REDACTED_MARKER in output


def test_extra_field_containing_restricted_value_is_redacted() -> None:
    logger, stream = _logger_with_captured_stream("test.restricted.extra")
    handler = logger.handlers[0]
    handler.setFormatter(logging.Formatter("%(message)s original_uid=%(original_uid)s"))

    logger.info(
        "deid_audit row written",
        extra={"original_uid": RestrictedValue("1.2.840.SECRET.999")},
    )

    output = stream.getvalue()
    assert "1.2.840.SECRET.999" not in output
    assert REDACTED_MARKER in output


def test_non_restricted_values_pass_through_unaffected() -> None:
    logger, stream = _logger_with_captured_stream("test.restricted.passthrough")
    logger.info("pseudo_uid=%s stage=%s", "provisional.abc123", "S2")
    output = stream.getvalue()
    assert "provisional.abc123" in output
    assert "S2" in output

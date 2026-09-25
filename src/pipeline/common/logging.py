"""Structured logging with a hard block on anything sourced from the `restricted` schema.

CLAUDE.md: "Never log tag values from the `restricted` schema." Two layers enforce this:

1. `RestrictedValue.__str__`/`__repr__` return a fixed redaction marker, so even naive
   f-string interpolation (`f"uid={original_uid}"`) never places the real value into the
   resulting string.
2. `RestrictedFieldFilter` runs on every handler and additionally strips any
   `RestrictedValue` instance that reaches a record un-stringified (lazy `%s` args, `extra=`
   fields), so it never reaches a formatter/handler either.

Any code reading a column from `restricted.uid_map`, `restricted.patient_map`, or
`restricted.deid_audit` must wrap the value in `RestrictedValue` before it could plausibly
flow into a log call.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

REDACTED_MARKER = "[REDACTED:restricted]"


class RestrictedValue:
    """Wraps a value sourced from the `restricted` schema. Never unwrap it into a log call;
    unwrap only at the point of legitimate use (e.g. writing to `restricted.*` tables).
    """

    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        self._value = value

    def reveal(self) -> object:
        """Explicit, greppable escape hatch for legitimate non-logging use."""
        return self._value

    def __repr__(self) -> str:
        return REDACTED_MARKER

    def __str__(self) -> str:
        return REDACTED_MARKER


def _redact(value: Any) -> Any:
    return REDACTED_MARKER if isinstance(value, RestrictedValue) else value


class RestrictedFieldFilter(logging.Filter):
    """Redacts any RestrictedValue that reaches a LogRecord un-stringified."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_redact(a) for a in record.args)
        for key, value in list(record.__dict__.items()):
            if isinstance(value, RestrictedValue):
                record.__dict__[key] = REDACTED_MARKER
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, sort_keys=True)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(RestrictedFieldFilter())
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

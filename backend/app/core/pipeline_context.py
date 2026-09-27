"""Pipeline fields merged into every structured log line.

This is not a second tracing system. ``correlation_id`` is the existing
ingestion-event field (defaults to the run id). Bind it at the start of an
operation; ``log_event`` picks the fields up automatically.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

_FIELDS: ContextVar[Dict[str, Any]] = ContextVar("pipeline_fields", default={})

_KEYS = (
    "component", "operation", "domain", "source", "run_id",
    "processing_job_id", "correlation_id", "status",
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def pipeline_fields() -> Dict[str, Any]:
    return dict(_FIELDS.get())


@contextmanager
def pipeline_bind(**fields: Optional[Any]) -> Iterator[Dict[str, Any]]:
    current = dict(_FIELDS.get())
    for key, value in fields.items():
        if key not in _KEYS or value is None or value == "":
            continue
        current[key] = str(value)
    if "correlation_id" not in current:
        current["correlation_id"] = new_correlation_id()
    token = _FIELDS.set(current)
    try:
        yield current
    finally:
        _FIELDS.reset(token)

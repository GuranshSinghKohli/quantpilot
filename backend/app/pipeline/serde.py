"""Kafka wire format: UTF-8 JSON of the envelope, plus routing headers.

Decoding checks in order (each failure is permanent and names its DLQ reason):
size → UTF-8/JSON → schema_version supported → envelope structure.
"""

from __future__ import annotations

import json
from typing import List, Tuple, Union

from pydantic import ValidationError

from app.pipeline.contracts import (
    EVENT_ADAPTER,
    SUPPORTED_SCHEMA_VERSIONS,
    CompanyEvent,
    DeadLetter,
    MarketEvent,
    NewsEvent,
)
from app.pipeline.errors import FailureReason, PermanentError

Event = Union[NewsEvent, MarketEvent, CompanyEvent]
Headers = List[Tuple[str, bytes]]


def encode_event(event: Event, max_bytes: int) -> bytes:
    data = event.model_dump_json().encode("utf-8")
    if len(data) > max_bytes:
        raise PermanentError(
            FailureReason.OVERSIZE,
            f"event {event.event_id} is {len(data)} bytes; limit {max_bytes}",
        )
    return data


def event_headers(event: Event) -> Headers:
    return [
        ("schema_version", str(event.schema_version).encode()),
        ("domain", event.domain.encode()),
        ("source", event.source.encode()),
        ("event_id", event.event_id.encode()),
        ("run_id", str(event.run_id).encode()),
        ("correlation_id", (event.correlation_id or str(event.run_id)).encode()),
    ]


def decode_event(data: bytes, max_bytes: int) -> Event:
    if len(data) > max_bytes:
        raise PermanentError(FailureReason.OVERSIZE, f"message is {len(data)} bytes; limit {max_bytes}")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PermanentError(FailureReason.DESERIALIZE, f"not UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise PermanentError(FailureReason.DESERIALIZE, "message is not a JSON object")

    version = document.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PermanentError(
            FailureReason.UNSUPPORTED_SCHEMA_VERSION,
            f"schema_version {version!r} not in {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )
    try:
        return EVENT_ADAPTER.validate_python(document)
    except ValidationError as exc:
        # Separate a bad envelope from a bad record so the DLQ says which layer broke.
        record_errors = [e for e in exc.errors() if len(e["loc"]) > 1 and e["loc"][1] == "record"]
        reason = (
            FailureReason.PAYLOAD_INVALID
            if record_errors and len(record_errors) == exc.error_count()
            else FailureReason.ENVELOPE_INVALID
        )
        raise PermanentError(reason, f"{exc.error_count()} validation error(s)", _errors(exc)) from exc


def encode_dead_letter(letter: DeadLetter) -> bytes:
    return letter.model_dump_json().encode("utf-8")


def decode_dead_letter(data: bytes) -> DeadLetter:
    return DeadLetter.model_validate_json(data)


def _errors(exc: ValidationError) -> list:
    return [
        {"loc": [str(part) for part in e["loc"]], "type": e["type"], "msg": e["msg"]}
        for e in exc.errors(include_url=False, include_input=False)
    ]

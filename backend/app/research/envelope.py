"""Research Kafka envelope. Same v1 fields as ingestion, without source bodies.

Delivery is at-least-once. Consumers are idempotent.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1
_MAX_ERROR = 500


class ResearchPayload(BaseModel):
    """Bounded message body. Identifiers and status only."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["request", "completed", "dlq"]
    research_id: Optional[str] = Field(default=None, max_length=64)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    request_version: str = Field(default="v1", max_length=16)
    status: Optional[str] = Field(default=None, max_length=32)
    completed_at: Optional[str] = Field(default=None, max_length=40)
    error_category: Optional[str] = Field(default=None, max_length=32)
    error_message: Optional[str] = Field(default=None, max_length=_MAX_ERROR)
    attempt_count: Optional[int] = Field(default=None, ge=0, le=1000)
    original_event_id: Optional[str] = Field(default=None, max_length=64)
    failed_at: Optional[str] = Field(default=None, max_length=40)


class ResearchEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(min_length=64, max_length=64)
    source: str = Field(default="quantpilot.research", max_length=64)
    source_type: str = Field(default="research_job", max_length=32)
    external_id: str = Field(min_length=1, max_length=64)
    occurred_at: str
    ingested_at: str
    content_hash: str = Field(min_length=64, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=128)
    payload: ResearchPayload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_id_for(kind: str, *parts: str) -> str:
    raw = "|".join([kind, *parts])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hash(payload: ResearchPayload) -> str:
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_event(
    payload: ResearchPayload,
    *,
    correlation_id: str,
    external_id: str,
    event_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> ResearchEvent:
    stamped = occurred_at or _now()
    return ResearchEvent(
        event_id=event_id or event_id_for(payload.kind, external_id, payload.idempotency_key or "", payload.status or ""),
        external_id=external_id[:64],
        occurred_at=stamped,
        ingested_at=stamped,
        content_hash=content_hash(payload),
        correlation_id=correlation_id[:128],
        payload=payload,
    )


def encode_event(event: ResearchEvent, max_bytes: int) -> bytes:
    data = event.model_dump_json().encode("utf-8")
    if len(data) > max_bytes:
        raise ValueError(f"research event is {len(data)} bytes; limit {max_bytes}")
    return data


def decode_event(data: bytes, max_bytes: int) -> ResearchEvent:
    if len(data) > max_bytes:
        raise ValueError(f"message is {len(data)} bytes; limit {max_bytes}")
    document: Dict[str, Any] = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("message is not a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    return ResearchEvent.model_validate(document)

"""Build dead letters that keep the original bytes and the failure context."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.pipeline.contracts import DeadLetter
from app.pipeline.errors import FailureReason, PermanentError, RetryExhausted

# Leave headroom under the broker limit for the DLQ envelope around the payload.
_ENVELOPE_HEADROOM = 16 * 1024
_DLQ_NAMESPACE = uuid.UUID("5d0c8f7e-0b8e-4d1a-9f4e-7a51c0de0d1a")


def deterministic_dlq_id(component: str, topic: str, partition: int, offset: int, reason: str) -> uuid.UUID:
    """Same failed delivery → same id, so a redelivered failure is recognisable
    as a copy of the dead letter already published."""
    return uuid.uuid5(_DLQ_NAMESPACE, f"{component}|{topic}|{partition}|{offset}|{reason}")


def failure_reason(error: BaseException) -> FailureReason:
    return _classify(error)[0]


def dead_letter(
    *,
    component: str,
    error: BaseException,
    original_value: bytes,
    max_bytes: int,
    first_failed_at: datetime,
    source: Optional[str] = None,
    event_id: Optional[str] = None,
    run_id: Optional[uuid.UUID] = None,
    original_topic: Optional[str] = None,
    partition: Optional[int] = None,
    offset: Optional[int] = None,
    original_key: Optional[bytes] = None,
    now: Optional[datetime] = None,
    dlq_id: Optional[uuid.UUID] = None,
) -> DeadLetter:
    reason, retry_count, root = _classify(error)
    budget = max(max_bytes - _ENVELOPE_HEADROOM, 1024)
    truncated = len(original_value) > budget
    payload = original_value[:budget]
    try:
        text, encoding = payload.decode("utf-8"), "utf8"
    except UnicodeDecodeError:
        text, encoding = base64.b64encode(payload).decode("ascii"), "base64"

    return DeadLetter(
        dlq_id=dlq_id or uuid.uuid4(),
        component=component,
        failure_reason=reason.value,
        error_type=type(root).__name__,
        error_message=str(root)[:4000],
        error_details=getattr(root, "details", []) or [],
        source=source,
        event_id=event_id,
        run_id=run_id,
        original_topic=original_topic,
        partition=partition,
        offset=offset,
        original_key=original_key.decode("utf-8", errors="replace") if original_key else None,
        original_value=text,
        value_encoding=encoding,
        original_truncated=truncated,
        retry_count=retry_count,
        first_failed_at=first_failed_at,
        failed_at=now or datetime.now(timezone.utc),
    )


def _classify(error: BaseException):
    if isinstance(error, RetryExhausted):
        return FailureReason.RETRIES_EXHAUSTED, error.retry_count, error.last_error
    if isinstance(error, PermanentError):
        return error.reason, 0, error
    # Anything unclassified is recorded as payload-invalid rather than dropped.
    return FailureReason.PAYLOAD_INVALID, 0, error

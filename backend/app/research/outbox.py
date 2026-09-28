"""Transactional outbox for research Kafka messages.

The job row and the outbox row commit together. A publisher then sends the
message. At-least-once delivery with idempotent processing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import ResearchJobStatus
from app.domain.models import ResearchJob, ResearchOutbox
from app.pipeline.kafka.producer import DeliveryFailed, OutboundMessage
from app.pipeline.retry import RetryPolicy
from app.research.envelope import ResearchEvent, ResearchPayload, build_event, encode_event, event_id_for
from app.research.metrics import RESEARCH_METRICS
from app.research.safety import sanitize


class QueueFull(RuntimeError):
    """Too many research jobs are waiting or running."""


class Publisher(Protocol):
    def publish(self, message: OutboundMessage): ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    *,
    topic: str,
    kind: str,
    payload: ResearchPayload,
    correlation_id: str,
    external_id: str,
    research_job_id: Optional[uuid.UUID] = None,
    event_id: Optional[str] = None,
) -> ResearchOutbox:
    """Insert one outbox row. A repeat of the same event_id returns the existing row."""
    eid = event_id or event_id_for(
        kind,
        external_id,
        payload.idempotency_key or "",
        payload.status or "",
        payload.original_event_id or "",
        str(payload.attempt_count or 0),
    )
    existing = db.scalar(select(ResearchOutbox).where(ResearchOutbox.event_id == eid))
    if existing is not None:
        return existing
    event = build_event(payload, correlation_id=correlation_id, external_id=external_id, event_id=eid)
    row = ResearchOutbox(
        id=uuid.uuid4(),
        research_job_id=research_job_id,
        event_id=eid,
        topic=topic,
        kind=kind,
        status="pending",
        payload=event.model_dump(mode="json"),
        correlation_id=correlation_id[:128],
        attempts=0,
        available_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def claim_pending(
    db: Session,
    worker_id: str,
    *,
    limit: int,
    lease_seconds: int,
    now: Optional[datetime] = None,
) -> List[ResearchOutbox]:
    moment = now or _now()
    stmt = (
        select(ResearchOutbox)
        .where(
            ResearchOutbox.status == "pending",
            ResearchOutbox.available_at <= moment,
            or_(ResearchOutbox.locked_until.is_(None), ResearchOutbox.locked_until < moment),
        )
        .order_by(ResearchOutbox.available_at)
        .limit(limit)
    )
    if db.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    rows = list(db.scalars(stmt))
    until = moment + timedelta(seconds=lease_seconds)
    for row in rows:
        row.locked_by = worker_id
        row.locked_until = until
    db.flush()
    return rows


def publish_pending(
    db: Session,
    publisher: Publisher,
    *,
    worker_id: str,
    batch: int,
    lease_seconds: int,
    max_bytes: int,
    policy: RetryPolicy,
    now: Optional[datetime] = None,
) -> int:
    """Publish a batch. Returns how many events were marked published."""
    moment = now or _now()
    rows = claim_pending(db, worker_id, limit=batch, lease_seconds=lease_seconds, now=moment)
    pending_count = db.scalar(
        select(func.count()).select_from(ResearchOutbox).where(ResearchOutbox.status == "pending")
    )
    RESEARCH_METRICS.set(
        "research_outbox_pending",
        float(pending_count or 0),
        operation="outbox",
        status="pending",
        component="research",
    )
    published = 0
    for row in rows:
        started = moment
        try:
            event = ResearchEvent.model_validate(row.payload)
            body = encode_event(event, max_bytes)
            key = event.payload.research_id or event.external_id
            publisher.publish(OutboundMessage(topic=row.topic, key=key, value=body))
        except DeliveryFailed as exc:
            _fail_publish(row, policy, exc, moment)
            continue
        except Exception as exc:
            _fail_publish(row, policy, exc, moment)
            continue
        row.status = "published"
        row.published_at = moment
        row.locked_by = None
        row.locked_until = None
        row.error = None
        published += 1
        if row.kind == "request" and row.research_job_id is not None:
            job = db.get(ResearchJob, row.research_job_id)
            if job is not None and job.status == ResearchJobStatus.PUBLISH_PENDING.value:
                job.status = ResearchJobStatus.QUEUED.value
        RESEARCH_METRICS.inc(
            "research_queue_published_total",
            1,
            operation="outbox",
            status="published",
            component="research",
        )
        RESEARCH_METRICS.observe(
            "research_queue_publish_seconds",
            max((_now() - started).total_seconds(), 0.0),
            operation="outbox",
            status="published",
            component="research",
        )
    db.flush()
    return published


def _fail_publish(row: ResearchOutbox, policy: RetryPolicy, exc: BaseException, moment: datetime) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.error = sanitize(str(exc))
    row.locked_by = None
    row.locked_until = None
    category = "kafka" if isinstance(exc, DeliveryFailed) else "publish"
    RESEARCH_METRICS.inc(
        "research_queue_publish_failures_total",
        1,
        operation="outbox",
        status="failed",
        error_category=category,
        component="research",
    )
    RESEARCH_METRICS.inc(
        "research_outbox_publish_failures_total",
        1,
        operation="outbox",
        status="failed",
        error_category=category,
        component="research",
    )
    if row.attempts >= policy.max_attempts:
        row.status = "failed"
        if row.research_job_id is not None and row.kind == "request":
            job = row.research_job
            if job is not None and job.status == ResearchJobStatus.PUBLISH_PENDING.value:
                job.status = ResearchJobStatus.FAILED.value
                job.error_category = "publish"
                job.error = row.error
                job.finished_at = moment
        return
    delay = policy.delay(row.attempts, rand=lambda: 1.0)
    row.available_at = moment + timedelta(seconds=delay)
    row.status = "pending"

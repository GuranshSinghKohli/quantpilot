"""Research worker. Calls run_research(); it does not reimplement the graph.

At-least-once Kafka delivery. Processing is idempotent.
Ordering holds only for messages that share a research_id partition key.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.pipeline_context import pipeline_bind
from app.domain.enums import ResearchJobStatus
from app.domain.models import ResearchJob
from app.observability.logger import get_logger, log_event
from app.pipeline.retry import RetryPolicy, is_transient
from app.research.envelope import ResearchPayload, decode_event
from app.research.llm import LLMError
from app.research.metrics import RESEARCH_METRICS
from app.research.outbox import enqueue
from app.research.persist import persist_result
from app.research.safety import sanitize
from app.research.service import ResearchUnavailable, run_research
from app.retrieval.package import RequestOversized, ResearchRequest

logger = get_logger("research.worker")

_TERMINAL = {
    ResearchJobStatus.SUCCEEDED.value,
    ResearchJobStatus.PARTIAL.value,
    ResearchJobStatus.FAILED.value,
    ResearchJobStatus.CANCELLED.value,
}
_NON_RETRYABLE = {"auth", "malformed", "invalid_request", "oversized", "permanent"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _policy(settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.RESEARCH_MAX_ATTEMPTS,
        base_delay=settings.RESEARCH_RETRY_BASE_SECONDS,
        max_delay=settings.RESEARCH_RETRY_MAX_SECONDS,
        jitter=False,
    )


def renew_lease(db: Session, job_id: uuid.UUID, worker_id: str, lease_seconds: int) -> bool:
    job = db.get(ResearchJob, job_id)
    if job is None or job.worker_id != worker_id or job.status != ResearchJobStatus.RUNNING.value:
        return False
    moment = _now()
    job.heartbeat_at = moment
    job.lease_until = moment + timedelta(seconds=lease_seconds)
    db.flush()
    return True


def claim_job(db: Session, job: ResearchJob, worker_id: str, settings, *, now: Optional[datetime] = None) -> bool:
    """Take ownership. A live lease held by someone else is left alone.

    PostgreSQL locks the row. SQLite serializes writers; tests flush so a second
    claim in the same session sees the lease.
    """
    moment = now or _now()
    if db.get_bind().dialect.name == "postgresql":
        locked = db.scalar(select(ResearchJob).where(ResearchJob.id == job.id).with_for_update())
        if locked is None:
            return False
        job = locked
    else:
        db.refresh(job)
    if job.status in _TERMINAL:
        return False
    if job.status == ResearchJobStatus.PUBLISH_PENDING.value:
        job.status = ResearchJobStatus.QUEUED.value
    lease_until = _aware(job.lease_until)
    if (
        job.status == ResearchJobStatus.RUNNING.value
        and lease_until is not None
        and lease_until > moment
    ):
        return False
    next_attempt = _aware(job.next_attempt_at)
    if job.status == ResearchJobStatus.QUEUED.value and next_attempt and next_attempt > moment:
        return False
    if job.status not in {ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value}:
        return False
    reclaimed = job.status == ResearchJobStatus.RUNNING.value
    job.status = ResearchJobStatus.RUNNING.value
    job.worker_id = worker_id
    job.heartbeat_at = moment
    job.lease_until = moment + timedelta(seconds=settings.RESEARCH_LEASE_SECONDS)
    job.started_at = job.started_at or moment
    job.attempts = (job.attempts or 0) + 1
    queued_at = _aware(job.queued_at)
    if queued_at is not None:
        RESEARCH_METRICS.observe(
            "research_job_queue_wait_seconds",
            max((moment - queued_at).total_seconds(), 0.0),
            operation="worker",
            status="running",
            component="research",
        )
    if reclaimed:
        RESEARCH_METRICS.inc(
            "research_job_reclaimed_total",
            1,
            operation="lease",
            status="reclaimed",
            component="research",
        )
    db.flush()
    return True


def _completion(db: Session, job: ResearchJob, settings) -> None:
    enqueue(
        db,
        topic=settings.KAFKA_TOPIC_RESEARCH_COMPLETED,
        kind="completed",
        payload=ResearchPayload(
            kind="completed",
            research_id=str(job.id),
            idempotency_key=job.idempotency_key,
            request_version=settings.RESEARCH_REQUEST_VERSION,
            status=job.status,
            completed_at=(job.finished_at or _now()).isoformat(),
        ),
        correlation_id=job.correlation_id or str(job.id),
        external_id=str(job.id),
        research_job_id=job.id,
        event_id=None,
    )


def _dlq(db: Session, settings, *, research_id: Optional[str], correlation_id: str, category: str, message: str, attempts: int, original_event_id: Optional[str], job_id: Optional[uuid.UUID] = None) -> None:
    enqueue(
        db,
        topic=settings.KAFKA_TOPIC_RESEARCH_DLQ,
        kind="dlq",
        payload=ResearchPayload(
            kind="dlq",
            research_id=research_id,
            request_version=settings.RESEARCH_REQUEST_VERSION,
            error_category=category[:32],
            error_message=sanitize(message),
            attempt_count=attempts,
            original_event_id=original_event_id,
            failed_at=_now().isoformat(),
            status="failed",
        ),
        correlation_id=correlation_id or "unknown",
        external_id=research_id or original_event_id or "malformed",
        research_job_id=job_id,
    )
    RESEARCH_METRICS.inc(
        "research_worker_dlq_total",
        1,
        operation="dlq",
        status="failed",
        error_category=category[:32] or "unknown",
        component="research",
    )


def _release(job: ResearchJob) -> None:
    job.worker_id = None
    job.lease_until = None


def _retry_or_fail(db: Session, job: ResearchJob, settings, *, category: str, message: str, retryable: bool) -> None:
    job.error_category = category[:32]
    job.error = sanitize(message)
    if retryable and (job.attempts or 0) < settings.RESEARCH_MAX_ATTEMPTS:
        delay = _policy(settings).delay(job.attempts or 1)
        job.status = ResearchJobStatus.QUEUED.value
        job.next_attempt_at = _now() + timedelta(seconds=delay)
        _release(job)
        RESEARCH_METRICS.inc(
            "research_worker_retries_total",
            1,
            operation="retry",
            status="queued",
            error_category=job.error_category,
            component="research",
        )
        return
    moment = _now()
    job.status = ResearchJobStatus.FAILED.value
    job.finished_at = moment
    _release(job)
    _dlq(
        db,
        settings,
        research_id=str(job.id),
        correlation_id=job.correlation_id or str(job.id),
        category=category,
        message=message,
        attempts=job.attempts or 0,
        original_event_id=None,
        job_id=job.id,
    )
    _completion(db, job, settings)
    RESEARCH_METRICS.inc(
        "research_worker_failure_total",
        1,
        operation="worker",
        status="failed",
        error_category=job.error_category,
        component="research",
    )


def execute_job(db: Session, job: ResearchJob, settings, *, llm=None, embed=None) -> str:
    started = time.perf_counter()
    request = ResearchRequest.model_validate(job.request)
    with pipeline_bind(component="research", operation="worker", correlation_id=job.correlation_id or str(job.id)):
        log_event(
            logger,
            20,
            "RESEARCH_JOB_STARTED",
            extra_data={"component": "research", "operation": "worker", "status": "running", "research_id": str(job.id)},
        )
        try:
            result = run_research(db, request, llm=llm, embed_provider=embed, persist=False)
        except (ResearchUnavailable, LLMError) as exc:
            category = getattr(exc, "error_type", "llm_unavailable")
            _retry_or_fail(db, job, settings, category=category, message=str(exc), retryable=category not in _NON_RETRYABLE)
        except RequestOversized as exc:
            _retry_or_fail(db, job, settings, category="oversized", message=str(exc), retryable=False)
        except Exception as exc:
            _retry_or_fail(db, job, settings, category="transient" if is_transient(exc) else "failed", message=str(exc), retryable=is_transient(exc))
        else:
            persist_result(db, request, result, job=job)
            _release(job)
            if job.status in _TERMINAL:
                _completion(db, job, settings)
            label = {
                ResearchJobStatus.SUCCEEDED.value: "research_worker_success_total",
                ResearchJobStatus.PARTIAL.value: "research_worker_partial_total",
                ResearchJobStatus.FAILED.value: "research_worker_failure_total",
            }.get(job.status)
            if label:
                RESEARCH_METRICS.inc(label, 1, operation="worker", status=job.status, component="research")
        RESEARCH_METRICS.observe(
            "research_worker_execution_seconds",
            time.perf_counter() - started,
            operation="worker",
            status=job.status,
            component="research",
        )
    return job.status


def accept_message(db: Session, data: bytes, settings, *, worker_id: str) -> tuple[str, Optional[uuid.UUID]]:
    """Validate one untrusted payload and claim the job. Does not run the graph."""
    RESEARCH_METRICS.inc("research_worker_messages_total", 1, operation="worker", status="received", component="research")
    try:
        event = decode_event(data, settings.KAFKA_MAX_MESSAGE_BYTES)
    except Exception as exc:
        _dlq(
            db,
            settings,
            research_id=None,
            correlation_id="unknown",
            category="malformed",
            message=str(exc),
            attempts=1,
            original_event_id=None,
        )
        return "dlq", None
    payload = event.payload
    if payload.kind != "request" or not payload.research_id:
        _dlq(
            db,
            settings,
            research_id=payload.research_id,
            correlation_id=event.correlation_id,
            category="malformed",
            message="research request payload is incomplete",
            attempts=1,
            original_event_id=event.event_id,
        )
        return "dlq", None
    try:
        job_id = uuid.UUID(payload.research_id)
    except ValueError:
        _dlq(db, settings, research_id=None, correlation_id=event.correlation_id, category="malformed", message="research_id is not a uuid", attempts=1, original_event_id=event.event_id)
        return "dlq", None
    job = db.get(ResearchJob, job_id)
    if job is None:
        _dlq(db, settings, research_id=payload.research_id, correlation_id=event.correlation_id, category="unknown_job", message="research job not found", attempts=1, original_event_id=event.event_id)
        return "dlq", None
    if job.status in _TERMINAL:
        return "duplicate", None
    if not claim_job(db, job, worker_id, settings):
        return "leased", None
    return "execute", job.id


def run_once(db: Session, data: bytes, settings, *, worker_id: str, llm=None, embed=None) -> str:
    """Claim and execute inside the caller's transaction. Tests use this path."""
    outcome, job_id = accept_message(db, data, settings, worker_id=worker_id)
    if outcome != "execute" or job_id is None:
        return outcome
    job = db.get(ResearchJob, job_id)
    if job is None:
        return "dlq"
    return execute_job(db, job, settings, llm=llm, embed=embed)


def due_job_ids(db: Session, settings, *, now: Optional[datetime] = None) -> list[uuid.UUID]:
    moment = now or _now()
    return list(
        db.scalars(
            select(ResearchJob.id).where(
                or_(
                    (ResearchJob.status == ResearchJobStatus.RUNNING.value)
                    & (ResearchJob.lease_until.is_not(None))
                    & (ResearchJob.lease_until < moment),
                    (ResearchJob.status == ResearchJobStatus.QUEUED.value)
                    & (ResearchJob.next_attempt_at.is_not(None))
                    & (ResearchJob.next_attempt_at <= moment),
                )
            )
        )
    )


def dispatch_due(db: Session, settings, *, worker_id: str, llm=None, embed=None) -> int:
    """Reclaim stale running jobs and resume retries whose backoff has elapsed.

    Same-session helper for tests. ``serve`` commits the lease before execution.
    """
    moment = _now()
    ran = 0
    for job_id in due_job_ids(db, settings, now=moment):
        job = db.get(ResearchJob, job_id)
        if job is not None and claim_job(db, job, worker_id, settings, now=moment):
            execute_job(db, job, settings, llm=llm, embed=embed)
            ran += 1
    return ran


def _heartbeat_loop(session_factory, job_id: uuid.UUID, worker_id: str, lease_seconds: int, interval: float, stop: threading.Event) -> None:
    while not stop.wait(max(interval, 0.05)):
        try:
            with session_factory() as db:
                renew_lease(db, job_id, worker_id, lease_seconds)
                db.commit()
        except Exception:
            log_event(
                logger,
                30,
                "RESEARCH_LEASE_RENEW_FAILED",
                extra_data={"component": "research", "operation": "lease", "status": "running", "research_id": str(job_id)},
            )


def _execute_leased(session_factory, job_id: uuid.UUID, settings, *, worker_id: str, llm=None, embed=None) -> str:
    """Run a job whose lease is already committed. A second session renews the lease."""
    stop = threading.Event()
    beater = threading.Thread(
        target=_heartbeat_loop,
        args=(session_factory, job_id, worker_id, settings.RESEARCH_LEASE_SECONDS, settings.RESEARCH_HEARTBEAT_SECONDS, stop),
        daemon=True,
        name="research-lease",
    )
    beater.start()
    try:
        with session_factory() as db:
            job = db.get(ResearchJob, job_id)
            if job is None or job.status in _TERMINAL:
                return "duplicate"
            if job.worker_id != worker_id or job.status != ResearchJobStatus.RUNNING.value:
                return "leased"
            status = execute_job(db, job, settings, llm=llm, embed=embed)
            db.commit()
            return status
    finally:
        stop.set()
        beater.join(timeout=max(settings.RESEARCH_HEARTBEAT_SECONDS, 1) + 1)


def _claim_committed(session_factory, job_id: uuid.UUID, settings, *, worker_id: str) -> bool:
    with session_factory() as db:
        job = db.get(ResearchJob, job_id)
        if job is None or not claim_job(db, job, worker_id, settings):
            db.rollback()
            return False
        db.commit()
        return True


def serve(session_factory, consumer, publisher, settings, *, llm=None, embed=None, worker_id: str = "research-worker", max_messages: Optional[int] = None, idle_timeout_s: float = 30.0) -> int:
    """Publish the outbox, reclaim stale jobs, then consume.

    The lease commits before ``run_research``. The Kafka offset commits after the
    result transaction. Delivery is at-least-once with idempotent processing.
    """
    from app.research.outbox import publish_pending

    handled = 0
    started = time.monotonic()
    while max_messages is None or handled < max_messages:
        with session_factory() as db:
            publish_pending(
                db,
                publisher,
                worker_id=worker_id,
                batch=settings.RESEARCH_OUTBOX_BATCH,
                lease_seconds=settings.RESEARCH_LEASE_SECONDS,
                max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
                policy=_policy(settings),
            )
            due = due_job_ids(db, settings)
            db.commit()
        for job_id in due:
            if _claim_committed(session_factory, job_id, settings, worker_id=worker_id):
                _execute_leased(session_factory, job_id, settings, worker_id=worker_id, llm=llm, embed=embed)
        message = consumer.poll(settings.KAFKA_POLL_TIMEOUT_SECONDS)
        if message is None or message.error():
            if idle_timeout_s and (time.monotonic() - started) > idle_timeout_s:
                break
            continue
        with session_factory() as db:
            outcome, job_id = accept_message(db, message.value(), settings, worker_id=worker_id)
            db.commit()
        if outcome == "execute" and job_id is not None:
            _execute_leased(session_factory, job_id, settings, worker_id=worker_id, llm=llm, embed=embed)
        consumer.commit(message=message, asynchronous=False)
        handled += 1
        started = time.monotonic()
    return handled

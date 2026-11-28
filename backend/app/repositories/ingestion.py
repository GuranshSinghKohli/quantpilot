"""Ingestion tracking: sources, runs, per-event records, processing jobs.

Same contract as the other repositories: flush, never commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from sqlalchemy import bindparam, func, or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import (
    RECORD_DELIVERED,
    RECORD_RETRYABLE,
    RECORD_TRANSITIONS,
    CanonicalLoadStatus,
    IngestionRecordStatus,
    IngestionRunStatus,
    ProcessingJobStatus,
)
from app.domain.ingestion_models import IngestionRecord, IngestionRun, IngestionSource, ProcessingJob
from app.repositories._upsert import chunks, insert_ignore

_BATCH = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- sources ---------------------------------------------------------------------


def ensure_source(db: Session, *, key: str, domain: str, provider: str, description: str) -> IngestionSource:
    insert_ignore(
        db,
        IngestionSource,
        {"id": uuid.uuid4(), "key": key, "domain": domain, "provider": provider, "description": description},
        ["key"],
    )
    source = db.scalars(select(IngestionSource).where(IngestionSource.key == key)).one()
    if (source.domain, source.provider, source.description) != (domain, provider, description):
        source.domain, source.provider, source.description = domain, provider, description
        db.flush()
    return source


# --- runs ------------------------------------------------------------------------


def start_run(db: Session, *, source_id: uuid.UUID, trigger: str, params: Dict[str, Any]) -> IngestionRun:
    run = IngestionRun(
        id=uuid.uuid4(),
        source_id=source_id,
        trigger=trigger,
        params=params,
        status=IngestionRunStatus.RUNNING.value,
        started_at=_now(),
    )
    db.add(run)
    db.flush()
    return run


@dataclass
class RunCounters:
    received: int = 0
    valid: int = 0
    invalid: int = 0
    duplicate: int = 0
    published: int = 0
    publish_failed: int = 0


def finish_run(
    db: Session,
    run: IngestionRun,
    *,
    status: IngestionRunStatus,
    counters: RunCounters,
    error: Optional[str] = None,
) -> IngestionRun:
    finished = _now()
    run.status = status.value
    run.records_received = counters.received
    run.records_valid = counters.valid
    run.records_invalid = counters.invalid
    run.records_duplicate = counters.duplicate
    run.records_published = counters.published
    run.records_publish_failed = counters.publish_failed
    run.error = error
    run.finished_at = finished
    started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
    run.duration_ms = int((finished - started).total_seconds() * 1000)
    db.flush()
    return run


def run_progress(db: Session, run_id: uuid.UUID) -> Dict[str, int]:
    """Current status counts of the events first seen in this run."""
    rows = db.execute(
        select(IngestionRecord.status, func.count())
        .where(IngestionRecord.run_id == run_id)
        .group_by(IngestionRecord.status)
    ).all()
    return {status: count for status, count in rows}


# --- per-event records -------------------------------------------------------------


class Claim(StrEnum):
    NEW = "new"  # first time this event version is seen
    RETRY = "retry"  # seen before but never safely delivered: publish again
    DUPLICATE = "duplicate"  # already delivered: skip


@dataclass(frozen=True)
class EventRef:
    event_id: str
    domain: str
    external_id: str
    dedup_key: str
    content_hash: str
    schema_version: int


def claim_event(
    db: Session, ref: EventRef, *, run_id: uuid.UUID, source_id: uuid.UUID, topic: str
) -> Claim:
    """Register an event before publishing. Idempotent on ``event_id``."""
    created = insert_ignore(
        db,
        IngestionRecord,
        {
            "id": uuid.uuid4(),
            "event_id": ref.event_id,
            "run_id": run_id,
            "source_id": source_id,
            "domain": ref.domain,
            "external_id": ref.external_id,
            "dedup_key": ref.dedup_key,
            "content_hash": ref.content_hash,
            "schema_version": ref.schema_version,
            "status": IngestionRecordStatus.PENDING.value,
            "kafka_topic": topic,
            "first_seen_at": _now(),
        },
        ["event_id"],
    )
    if created:
        return Claim.NEW
    status = db.scalar(select(IngestionRecord.status).where(IngestionRecord.event_id == ref.event_id))
    if status in RECORD_DELIVERED:
        return Claim.DUPLICATE
    if status in RECORD_RETRYABLE:
        db.execute(
            update(IngestionRecord)
            .where(IngestionRecord.event_id == ref.event_id)
            .values(status=IngestionRecordStatus.PENDING.value, status_reason=None, updated_at=_now())
        )
        return Claim.RETRY
    raise ValueError(f"event {ref.event_id} has unexpected status {status!r}")


def advance_records(
    db: Session,
    target: IngestionRecordStatus,
    event_ids: Sequence[str],
    *,
    reason: Optional[str] = None,
    raw_uri: Optional[str] = None,
    processing_job_id: Optional[uuid.UUID] = None,
) -> int:
    """Move records forward to ``target`` (never backwards). Returns rows moved."""
    allowed = [s.value for s in RECORD_TRANSITIONS[target]]
    values: Dict[str, Any] = {"status": target.value, "updated_at": _now()}
    if reason is not None:
        values["status_reason"] = reason[:4000]
    if raw_uri is not None:
        values["raw_uri"] = raw_uri
    if processing_job_id is not None:
        values["processing_job_id"] = processing_job_id
    moved = 0
    for batch in chunks(list(event_ids), _BATCH):
        result = db.execute(
            update(IngestionRecord)
            .where(IngestionRecord.event_id.in_(batch), IngestionRecord.status.in_(allowed))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        moved += result.rowcount or 0
    return moved


def record_deliveries(db: Session, deliveries: Iterable[Tuple[str, int, int]]) -> int:
    """Mark events published with their Kafka (partition, offset)."""
    rows = [
        {"b_event_id": event_id, "b_partition": partition, "b_offset": offset}
        for event_id, partition, offset in deliveries
    ]
    if not rows:
        return 0
    table = IngestionRecord.__table__
    allowed = [s.value for s in RECORD_TRANSITIONS[IngestionRecordStatus.PUBLISHED]]
    stmt = (
        table.update()
        .where(table.c.event_id == bindparam("b_event_id"), or_(*(table.c.status == s for s in allowed)))
        .values(
            status=IngestionRecordStatus.PUBLISHED.value,
            kafka_partition=bindparam("b_partition"),
            kafka_offset=bindparam("b_offset"),
            updated_at=_now(),
        )
    )
    result = db.connection().execute(stmt, rows)
    return result.rowcount or 0


def record_landing(
    db: Session, event_id: str, *, raw_uri: str, topic: str, partition: int, offset: int
) -> int:
    """Mark an event landed in the raw lake with the Kafka position it was consumed
    from. Forward-only; the position is filled in only where the producer's
    delivery report has not already set it. Returns rows moved (0 or 1)."""
    table = IngestionRecord.__table__
    allowed = [s.value for s in RECORD_TRANSITIONS[IngestionRecordStatus.LANDED]]
    result = db.execute(
        update(IngestionRecord)
        .where(IngestionRecord.event_id == event_id, IngestionRecord.status.in_(allowed))
        .values(
            status=IngestionRecordStatus.LANDED.value,
            status_reason=None,
            raw_uri=raw_uri,
            kafka_topic=func.coalesce(table.c.kafka_topic, topic),
            kafka_partition=func.coalesce(table.c.kafka_partition, partition),
            kafka_offset=func.coalesce(table.c.kafka_offset, offset),
            updated_at=_now(),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def get_record(db: Session, event_id: str) -> Optional[IngestionRecord]:
    return db.scalars(select(IngestionRecord).where(IngestionRecord.event_id == event_id)).first()


def existing_event_ids(db: Session, event_ids: Sequence[str]) -> set:
    found: set = set()
    for batch in chunks(list(event_ids), _BATCH):
        found.update(db.scalars(select(IngestionRecord.event_id).where(IngestionRecord.event_id.in_(batch))))
    return found


# --- processing jobs -------------------------------------------------------------


def start_processing_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    job_name: str,
    domain: str,
    mode: str,
    since=None,
    until=None,
    input_prefix: Optional[str] = None,
    input_objects: int = 0,
    code_version: Optional[str] = None,
    engine_version: Optional[str] = None,
) -> ProcessingJob:
    job = ProcessingJob(
        id=job_id,
        job_name=job_name,
        domain=domain,
        mode=mode,
        since=since,
        until=until,
        input_prefix=input_prefix,
        input_objects=input_objects,
        status=ProcessingJobStatus.RUNNING.value,
        code_version=code_version,
        engine_version=engine_version,
        started_at=_now(),
    )
    db.add(job)
    db.flush()
    return job


def finish_processing_job(
    db: Session,
    job: ProcessingJob,
    *,
    status: ProcessingJobStatus,
    metrics: Dict[str, Any],
    output_uri: Optional[str] = None,
    manifest_uri: Optional[str] = None,
    error: Optional[str] = None,
    spark_app_id: Optional[str] = None,
) -> ProcessingJob:
    finished = _now()
    job.status = status.value
    if spark_app_id is not None:
        job.spark_app_id = spark_app_id
    job.input_rows = int(metrics.get("input_rows", 0))
    job.rejected_rows = int(metrics.get("rejected_rows", 0))
    job.duplicates_removed = int(metrics.get("duplicates_removed", 0))
    job.output_rows = int(metrics.get("output_rows", 0))
    job.output_partitions = list(metrics.get("output_partitions", []))
    job.output_uri = output_uri
    job.manifest_uri = manifest_uri
    job.error = error
    job.finished_at = finished
    started = job.started_at if job.started_at.tzinfo else job.started_at.replace(tzinfo=timezone.utc)
    job.duration_ms = int((finished - started).total_seconds() * 1000)
    db.flush()
    return job


def start_load(db: Session, job: ProcessingJob) -> ProcessingJob:
    """Begin a canonical load attempt: the ``load_*`` columns now describe it."""
    job.load_status = CanonicalLoadStatus.RUNNING.value
    job.load_attempts = (job.load_attempts or 0) + 1
    job.load_started_at = _now()
    job.load_candidates = job.load_inserted = job.load_updated = 0
    job.load_stale = job.load_duplicates = job.load_failed = 0
    job.load_partitions = []
    job.load_error = None
    job.load_report_uri = None
    job.loaded_rows = job.loaded_at = job.load_duration_ms = None
    db.flush()
    return job


def finish_load(
    db: Session,
    job: ProcessingJob,
    *,
    status: CanonicalLoadStatus,
    counts: Dict[str, int],
    partitions: Sequence[str],
    error: Optional[str] = None,
    report_uri: Optional[str] = None,
) -> ProcessingJob:
    finished = _now()
    job.load_status = status.value
    job.load_candidates = int(counts.get("candidates", 0))
    job.load_inserted = int(counts.get("inserted", 0))
    job.load_updated = int(counts.get("updated", 0))
    job.load_stale = int(counts.get("stale", 0))
    job.load_duplicates = int(counts.get("duplicates", 0))
    job.load_failed = int(counts.get("failed", 0))
    job.loaded_rows = job.load_inserted + job.load_updated
    job.load_partitions = list(partitions)
    job.load_error = error[:8000] if error else None
    job.load_report_uri = report_uri
    job.loaded_at = finished
    started = job.load_started_at or finished
    started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
    job.load_duration_ms = int((finished - started).total_seconds() * 1000)
    db.flush()
    return job


# --- lineage ---------------------------------------------------------------------


def lineage(db: Session, event_id: str) -> Optional[Dict[str, Any]]:
    """source → ingestion run → raw record → processing job, for one event."""
    record = get_record(db, event_id)
    if record is None:
        return None
    job = db.get(ProcessingJob, record.processing_job_id) if record.processing_job_id else None
    return {
        "source": {"key": record.source.key, "provider": record.source.provider, "domain": record.source.domain},
        "ingestion_run": {
            "id": str(record.run.id),
            "status": record.run.status,
            "trigger": record.run.trigger,
            "started_at": _iso(record.run.started_at),
            "finished_at": _iso(record.run.finished_at),
        },
        "raw_record": {
            "event_id": record.event_id,
            "external_id": record.external_id,
            "dedup_key": record.dedup_key,
            "content_hash": record.content_hash,
            "schema_version": record.schema_version,
            "status": record.status,
            "raw_uri": record.raw_uri,
            "kafka": {
                "topic": record.kafka_topic,
                "partition": record.kafka_partition,
                "offset": record.kafka_offset,
            },
            "first_seen_at": _iso(record.first_seen_at),
        },
        "processing_job": None
        if job is None
        else {
            "id": str(job.id),
            "job_name": job.job_name,
            "status": job.status,
            "code_version": job.code_version,
            "engine_version": job.engine_version,
            "spark_app_id": job.spark_app_id,
            "output_uri": job.output_uri,
            "manifest_uri": job.manifest_uri,
            "load_status": job.load_status,
            "loaded_at": _iso(job.loaded_at),
            "load_report_uri": job.load_report_uri,
        },
    }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None

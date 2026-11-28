"""Latest pipeline activity per domain — the operator's first look."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.domain.enums import CanonicalLoadStatus, IngestionRunStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRecord, IngestionRun, IngestionSource, ProcessingJob
from app.pipeline.ops.dlq import dlq_summary
from app.pipeline.ops.probes import kafka_lag, probe_kafka, probe_lake


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _run_row(run: Optional[IngestionRun]) -> Optional[Dict[str, Any]]:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "source": run.source.key,
        "domain": run.source.domain,
        "status": run.status,
        "received": run.records_received,
        "published": run.records_published,
        "duplicate": run.records_duplicate,
        "invalid": run.records_invalid,
        "publish_failed": run.records_publish_failed,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "duration_ms": run.duration_ms,
        "error": run.error,
    }


def _job_row(job: Optional[ProcessingJob]) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    return {
        "id": str(job.id),
        "domain": job.domain,
        "status": job.status,
        "input_rows": job.input_rows,
        "output_rows": job.output_rows,
        "rejected_rows": job.rejected_rows,
        "duplicates_removed": job.duplicates_removed,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "duration_ms": job.duration_ms,
        "manifest_uri": job.manifest_uri,
        "load_status": job.load_status,
        "load_started_at": _iso(job.load_started_at),
        "loaded_at": _iso(job.loaded_at),
        "load_duration_ms": job.load_duration_ms,
        "load_inserted": job.load_inserted,
        "load_updated": job.load_updated,
        "load_stale": job.load_stale,
        "load_duplicates": job.load_duplicates,
        "load_failed": job.load_failed,
        "error": job.error or job.load_error,
    }


def pipeline_status(db: Session, *, include_kafka: bool = True, include_dlq: bool = True) -> Dict[str, Any]:
    domains = [row[0] for row in db.execute(select(IngestionSource.domain).distinct()).all()]
    for extra in ("news", "market", "company"):
        if extra not in domains:
            domains.append(extra)

    latest_runs, latest_jobs, latest_loads = {}, {}, {}
    for domain in domains:
        run = db.scalars(
            select(IngestionRun)
            .join(IngestionSource)
            .where(IngestionSource.domain == domain)
            .order_by(desc(IngestionRun.created_at))
            .limit(1)
        ).first()
        latest_runs[domain] = _run_row(run)
        job = db.scalars(
            select(ProcessingJob).where(ProcessingJob.domain == domain)
            .order_by(desc(ProcessingJob.started_at)).limit(1)
        ).first()
        latest_jobs[domain] = _job_row(job)
        loaded = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.domain == domain, ProcessingJob.load_status.is_not(None))
            .order_by(desc(ProcessingJob.load_started_at)).limit(1)
        ).first()
        latest_loads[domain] = _job_row(loaded)

    running_runs = list(db.scalars(
        select(IngestionRun).where(IngestionRun.status == IngestionRunStatus.RUNNING.value)
        .order_by(desc(IngestionRun.started_at)).limit(20)
    ))
    running_jobs = list(db.scalars(
        select(ProcessingJob).where(ProcessingJob.status == ProcessingJobStatus.RUNNING.value)
        .order_by(desc(ProcessingJob.started_at)).limit(20)
    ))
    running_loads = list(db.scalars(
        select(ProcessingJob).where(ProcessingJob.load_status == CanonicalLoadStatus.RUNNING.value)
        .order_by(desc(ProcessingJob.load_started_at)).limit(20)
    ))
    failed_runs = list(db.scalars(
        select(IngestionRun).where(IngestionRun.status.in_([
            IngestionRunStatus.FAILED.value, IngestionRunStatus.PARTIAL.value,
        ])).order_by(desc(IngestionRun.finished_at)).limit(20)
    ))
    failed_jobs = list(db.scalars(
        select(ProcessingJob).where(
            (ProcessingJob.status == ProcessingJobStatus.FAILED.value)
            | (ProcessingJob.load_status == CanonicalLoadStatus.FAILED.value)
        ).order_by(desc(ProcessingJob.finished_at)).limit(20)
    ))

    last_process = db.scalars(
        select(ProcessingJob).where(ProcessingJob.status == ProcessingJobStatus.COMPLETED.value)
        .order_by(desc(ProcessingJob.finished_at)).limit(1)
    ).first()
    last_load = db.scalars(
        select(ProcessingJob).where(ProcessingJob.load_status == CanonicalLoadStatus.COMPLETE.value)
        .order_by(desc(ProcessingJob.loaded_at)).limit(1)
    ).first()

    record_counts = {
        status: n
        for status, n in db.execute(
            select(IngestionRecord.status, func.count()).group_by(IngestionRecord.status)
        ).all()
    }

    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_ingestion": latest_runs,
        "latest_processing": latest_jobs,
        "latest_canonical_load": latest_loads,
        "running": {
            "ingestion_runs": [_run_row(r) for r in running_runs],
            "processing_jobs": [_job_row(j) for j in running_jobs],
            "canonical_loads": [_job_row(j) for j in running_loads],
        },
        "failed": {
            "ingestion_runs": [_run_row(r) for r in failed_runs],
            "jobs": [_job_row(j) for j in failed_jobs],
        },
        "last_successful_processing_at": _iso(last_process.finished_at) if last_process else None,
        "last_successful_canonical_load_at": _iso(last_load.loaded_at) if last_load else None,
        "ingestion_records": record_counts,
    }
    if include_kafka:
        out["kafka"] = probe_kafka()
        out["lake"] = probe_lake()
        out["kafka_lag"] = kafka_lag()
    if include_dlq:
        out["dlq"] = dlq_summary()
    return out

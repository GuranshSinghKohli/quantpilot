"""Report jobs stuck in RUNNING. Never mutates them."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.domain.enums import CanonicalLoadStatus, IngestionRunStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRun, ProcessingJob


def _age_s(started: Optional[datetime], now: datetime) -> Optional[float]:
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (now - started).total_seconds()


def stale_report(db: Session, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    stale_runs, stale_jobs, stale_loads = [], [], []

    for run in db.scalars(select(IngestionRun).where(IngestionRun.status == IngestionRunStatus.RUNNING.value)):
        age = _age_s(run.started_at, now)
        if age is not None and age > settings.PIPELINE_STALE_RUN_SECONDS:
            stale_runs.append({
                "id": str(run.id),
                "source": run.source.key if run.source else None,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "age_seconds": int(age),
                "threshold_seconds": settings.PIPELINE_STALE_RUN_SECONDS,
                "recovery": "confirm the ingest process is gone, then `python -m app.pipeline retry-ingestion "
                            f"{run.id}` (FAILED/PARTIAL only) or start a new ingest with the same window",
            })

    for job in db.scalars(select(ProcessingJob).where(ProcessingJob.status == ProcessingJobStatus.RUNNING.value)):
        age = _age_s(job.started_at, now)
        if age is not None and age > settings.PIPELINE_STALE_JOB_SECONDS:
            stale_jobs.append({
                "id": str(job.id),
                "domain": job.domain,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "age_seconds": int(age),
                "threshold_seconds": settings.PIPELINE_STALE_JOB_SECONDS,
                "recovery": "confirm no Spark driver holds the domain lock, then "
                            f"`python -m app.pipeline reprocess-job {job.id}`",
            })

    for job in db.scalars(
        select(ProcessingJob).where(ProcessingJob.load_status == CanonicalLoadStatus.RUNNING.value)
    ):
        age = _age_s(job.load_started_at, now)
        if age is not None and age > settings.PIPELINE_STALE_LOAD_SECONDS:
            stale_loads.append({
                "id": str(job.id),
                "domain": job.domain,
                "load_started_at": job.load_started_at.isoformat() if job.load_started_at else None,
                "age_seconds": int(age),
                "threshold_seconds": settings.PIPELINE_STALE_LOAD_SECONDS,
                "recovery": "confirm no load process holds the domain lock, then "
                            f"`python -m app.pipeline retry-load {job.id}`",
            })

    return {
        "generated_at": now.isoformat(),
        "stale": bool(stale_runs or stale_jobs or stale_loads),
        "ingestion_runs": stale_runs,
        "processing_jobs": stale_jobs,
        "canonical_loads": stale_loads,
        "note": "Reported only. Nothing was marked FAILED. See docs/PHASE2_8_PLAN.md §5.",
    }

"""Per-domain count chain: where did the source records go?"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import CanonicalLoadStatus
from app.domain.ingestion_models import IngestionRecord, IngestionRun, IngestionSource, ProcessingJob
from app.domain.models import Company
from app.domain.ingestion_models import MarketBar, NewsArticle


def quality_summary(db: Session, *, domain: Optional[str] = None) -> Dict[str, Any]:
    domains = [domain] if domain else ["news", "market", "company"]
    out = {}
    for name in domains:
        out[name] = _domain(db, name)
    return {"domains": out}


def _domain(db: Session, domain: str) -> Dict[str, Any]:
    runs = db.execute(
        select(
            func.coalesce(func.sum(IngestionRun.records_received), 0),
            func.coalesce(func.sum(IngestionRun.records_invalid), 0),
            func.coalesce(func.sum(IngestionRun.records_duplicate), 0),
            func.coalesce(func.sum(IngestionRun.records_published), 0),
        ).select_from(IngestionRun).join(IngestionSource).where(IngestionSource.domain == domain)
    ).one()
    records = {
        status: n
        for status, n in db.execute(
            select(IngestionRecord.status, func.count())
            .where(IngestionRecord.domain == domain)
            .group_by(IngestionRecord.status)
        ).all()
    }
    jobs = db.execute(
        select(
            func.coalesce(func.sum(ProcessingJob.input_rows), 0),
            func.coalesce(func.sum(ProcessingJob.rejected_rows), 0),
            func.coalesce(func.sum(ProcessingJob.output_rows), 0),
            func.coalesce(func.sum(ProcessingJob.duplicates_removed), 0),
            func.coalesce(func.sum(ProcessingJob.load_inserted), 0),
            func.coalesce(func.sum(ProcessingJob.load_updated), 0),
            func.coalesce(func.sum(ProcessingJob.load_stale), 0),
            func.coalesce(func.sum(ProcessingJob.load_duplicates), 0),
            func.coalesce(func.sum(ProcessingJob.load_failed), 0),
        ).where(ProcessingJob.domain == domain)
    ).one()
    latest_load = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.domain == domain, ProcessingJob.load_status == CanonicalLoadStatus.COMPLETE.value)
        .order_by(ProcessingJob.loaded_at.desc())
        .limit(1)
    ).first()
    canonical = 0
    if domain == "company":
        canonical = db.scalar(select(func.count()).select_from(Company)) or 0
    elif domain == "market":
        canonical = db.scalar(select(func.count()).select_from(MarketBar)) or 0
    elif domain == "news":
        canonical = db.scalar(select(func.count()).select_from(NewsArticle)) or 0
    return {
        "records_ingested": int(runs[0]),
        "invalid_records": int(runs[1]),
        "duplicates": int(runs[2]),
        "published": int(runs[3]),
        "records_by_status": records,
        "processed": records.get("processed", 0) + records.get("loaded", 0),
        "rejected": int(jobs[1]),
        "processing_output_rows": int(jobs[2]),
        "processing_duplicates_removed": int(jobs[3]),
        "canonical_inserted": int(jobs[4]),
        "canonical_updated": int(jobs[5]),
        "canonical_stale": int(jobs[6]),
        "canonical_duplicate": int(jobs[7]),
        "canonical_failed": int(jobs[8]),
        "canonical_rows_now": canonical,
        "latest_complete_load": None if latest_load is None else {
            "job_id": str(latest_load.id),
            "loaded_at": latest_load.loaded_at.isoformat() if latest_load.loaded_at else None,
            "inserted": latest_load.load_inserted,
            "updated": latest_load.load_updated,
            "stale": latest_load.load_stale,
            "duplicates": latest_load.load_duplicates,
            "failed": latest_load.load_failed,
        },
    }

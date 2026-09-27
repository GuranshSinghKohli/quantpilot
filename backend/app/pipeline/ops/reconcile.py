"""Scoped pipeline reconciliation. Never lists the whole lake."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import CanonicalLoadStatus, IngestionRecordStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRecord, MarketBar, NewsArticle, ProcessingJob
from app.domain.models import Company
from app.pipeline.lake import ObjectStore
from app.pipeline.lake.keys import manifest_key
from app.pipeline.spark.manifest import MANIFEST_VERSION


def reconcile(
    db: Session,
    store: ObjectStore,
    *,
    domain: Optional[str] = None,
    job_id: Optional[uuid.UUID] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> Dict[str, Any]:
    problems: List[str] = []
    checked: Dict[str, Any] = {}
    records = _records(db, domain=domain, job_id=job_id, since=since, until=until)
    checked["records"] = len(records)
    problems.extend(_record_invariants(db, records))
    jobs = _jobs(db, domain=domain, job_id=job_id)
    checked["jobs"] = len(jobs)
    problems.extend(_job_invariants(jobs))
    for job in jobs:
        problems.extend(_manifest_files(store, job))
        problems.extend(_load_counts(job))
    problems.extend(_canonical_lineage(db, domain=domain, job_id=job_id))
    return {
        "ok": not problems,
        "scope": {
            "domain": domain,
            "job_id": str(job_id) if job_id else None,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "checked": checked,
        "problems": problems,
    }


def _records(db, *, domain, job_id, since, until) -> List[IngestionRecord]:
    stmt = select(IngestionRecord)
    if domain:
        stmt = stmt.where(IngestionRecord.domain == domain)
    if job_id:
        stmt = stmt.where(IngestionRecord.processing_job_id == job_id)
    if since:
        stmt = stmt.where(IngestionRecord.first_seen_at >= datetime(since.year, since.month, since.day, tzinfo=timezone.utc))
    if until:
        end = datetime(until.year, until.month, until.day, 23, 59, 59, tzinfo=timezone.utc)
        stmt = stmt.where(IngestionRecord.first_seen_at <= end)
    return list(db.scalars(stmt.limit(50_000)))


def _jobs(db, *, domain, job_id) -> List[ProcessingJob]:
    if job_id:
        job = db.get(ProcessingJob, job_id)
        return [job] if job is not None else []
    # Without an explicit job, only the latest COMPLETED job per domain is
    # checked. Earlier jobs may have had their partitions overwritten.
    stmt = select(ProcessingJob).where(ProcessingJob.status == ProcessingJobStatus.COMPLETED.value)
    if domain:
        stmt = stmt.where(ProcessingJob.domain == domain)
        job = db.scalars(stmt.order_by(ProcessingJob.started_at.desc()).limit(1)).first()
        return [job] if job is not None else []
    latest = []
    for name in ("news", "market", "company"):
        job = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.domain == name, ProcessingJob.status == ProcessingJobStatus.COMPLETED.value)
            .order_by(ProcessingJob.started_at.desc()).limit(1)
        ).first()
        if job is not None:
            latest.append(job)
    return latest


def _record_invariants(db: Session, records: List[IngestionRecord]) -> List[str]:
    problems = []
    landed = {IngestionRecordStatus.LANDED.value, IngestionRecordStatus.PROCESSED.value,
              IngestionRecordStatus.LOADED.value}
    processed = {IngestionRecordStatus.PROCESSED.value, IngestionRecordStatus.LOADED.value}
    job_ids = {r.processing_job_id for r in records if r.processing_job_id}
    existing_jobs = set(db.scalars(select(ProcessingJob.id).where(ProcessingJob.id.in_(job_ids)))) if job_ids else set()
    for record in records:
        if record.status in landed and not record.raw_uri:
            problems.append(f"record {record.event_id[:12]} status={record.status} has no raw_uri")
        if record.status in processed and record.processing_job_id is None:
            problems.append(f"record {record.event_id[:12]} status={record.status} has no processing_job_id")
        if record.processing_job_id and record.processing_job_id not in existing_jobs:
            problems.append(f"record {record.event_id[:12]} points at missing job {record.processing_job_id}")
        if record.status == IngestionRecordStatus.PENDING.value and record.raw_uri:
            problems.append(f"record {record.event_id[:12]} is pending but already has raw_uri")
    return problems[:200]


def _job_invariants(jobs: List[ProcessingJob]) -> List[str]:
    problems = []
    for job in jobs:
        if job.load_status == CanonicalLoadStatus.COMPLETE.value:
            if job.status != ProcessingJobStatus.COMPLETED.value:
                problems.append(f"job {job.id} load COMPLETE without processing COMPLETED (status={job.status})")
            if not job.manifest_uri:
                problems.append(f"job {job.id} load COMPLETE without a manifest")
            if job.output_rows == 0 and job.load_candidates > 0:
                problems.append(f"job {job.id} load COMPLETE with output_rows=0 but candidates={job.load_candidates}")
        if job.status == ProcessingJobStatus.FAILED.value and job.load_status == CanonicalLoadStatus.COMPLETE.value:
            problems.append(f"job {job.id} is processing FAILED but load COMPLETE")
        if job.status == ProcessingJobStatus.COMPLETED.value and job.finished_at is None:
            problems.append(f"job {job.id} COMPLETED without finished_at")
    return problems


def _manifest_files(store: ObjectStore, job: ProcessingJob) -> List[str]:
    if not job.manifest_uri or job.status != ProcessingJobStatus.COMPLETED.value:
        return []
    key = manifest_key(job.id)
    try:
        raw = store.get(key)
    except Exception as exc:
        return [f"job {job.id} manifest missing or unreadable: {type(exc).__name__}"]
    try:
        manifest = json.loads(raw)
    except ValueError:
        return [f"job {job.id} manifest is not JSON"]
    problems = []
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        problems.append(f"job {job.id} manifest version {manifest.get('manifest_version')}")
    if manifest.get("job_id") != str(job.id):
        problems.append(f"job {job.id} manifest job_id mismatch")
    if manifest.get("domain") != job.domain:
        problems.append(f"job {job.id} manifest domain mismatch")
    for key in manifest.get("input", {}).get("objects", []):
        if store.head(key) is None:
            problems.append(f"job {job.id} missing raw object {key}")
    for file in manifest.get("output", {}).get("files", []):
        info = store.head(file.get("key", ""))
        if info is None:
            problems.append(f"job {job.id} missing output {file.get('key')}")
        elif file.get("size") is not None and info.size != file["size"]:
            problems.append(f"job {job.id} {file.get('key')} size {info.size} != manifest {file['size']}")
    return problems


def _load_counts(job: ProcessingJob) -> List[str]:
    if job.load_status != CanonicalLoadStatus.COMPLETE.value:
        return []
    accounted = job.load_inserted + job.load_updated + job.load_stale + job.load_duplicates + job.load_failed
    if job.load_candidates != accounted:
        return [f"job {job.id} load candidates {job.load_candidates} != accounted {accounted}"]
    return []


def _canonical_lineage(db: Session, *, domain: Optional[str], job_id: Optional[uuid.UUID]) -> List[str]:
    problems = []
    event_ids = set(db.scalars(select(IngestionRecord.event_id)))
    job_ids = set(db.scalars(select(ProcessingJob.id)))

    def check(rows, label):
        for event_id, processing_job_id, raw_uri in rows:
            if event_id is None and processing_job_id is None:
                continue  # pre-pipeline row; not this layer's lineage
            if event_id not in event_ids:
                problems.append(f"{label} event_id {str(event_id)[:12]} has no ingestion_records row")
            if processing_job_id is not None and processing_job_id not in job_ids:
                problems.append(f"{label} processing_job_id {processing_job_id} is missing")
            if event_id and not raw_uri:
                problems.append(f"{label} event {str(event_id)[:12]} has empty raw_uri")

    if domain in (None, "market"):
        stmt = select(MarketBar.event_id, MarketBar.processing_job_id, MarketBar.raw_uri)
        if job_id:
            stmt = stmt.where(MarketBar.processing_job_id == job_id)
        check(db.execute(stmt.limit(20_000)).all(), "market_bars")
    if domain in (None, "company"):
        stmt = select(Company.event_id, Company.processing_job_id, Company.raw_uri)
        if job_id:
            stmt = stmt.where(Company.processing_job_id == job_id)
        check(db.execute(stmt.limit(20_000)).all(), "companies")
    if domain in (None, "news"):
        stmt = select(NewsArticle.event_id, NewsArticle.processing_job_id)
        if job_id:
            stmt = stmt.where(NewsArticle.processing_job_id == job_id)
        for event_id, processing_job_id in db.execute(stmt.limit(20_000)).all():
            if event_id not in event_ids:
                problems.append(f"news_articles event_id {event_id[:12]} has no ingestion_records row")
            if processing_job_id not in job_ids:
                problems.append(f"news_articles processing_job_id {processing_job_id} is missing")
    return problems[:200]

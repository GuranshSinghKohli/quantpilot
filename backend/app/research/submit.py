"""Create a durable research job and its outbox row. Does not run the graph."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import ResearchJobStatus
from app.domain.models import AgentRun, Report, ResearchClaim, ResearchJob, ResearchOutbox
from app.domain.schemas import ResearchJobIn
from app.repositories.research_jobs import IdempotencyConflict, create_job
from app.research.envelope import ResearchPayload
from app.research.outbox import QueueFull, enqueue
from app.research.persist import _job_type
from app.research.safety import sanitize
from app.retrieval.package import ResearchRequest

_ACTIVE = (
    ResearchJobStatus.PUBLISH_PENDING.value,
    ResearchJobStatus.QUEUED.value,
    ResearchJobStatus.RUNNING.value,
)


def submit_research(
    db: Session,
    request: ResearchRequest,
    *,
    owner_key: str,
    idempotency_key: str,
    correlation_id: str,
    settings,
) -> ResearchJob:
    existing = db.scalar(
        select(ResearchJob).where(
            ResearchJob.owner_key == owner_key,
            ResearchJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        payload = request.model_dump(mode="json")
        if existing.request != payload:
            raise IdempotencyConflict(f"Idempotency key {idempotency_key!r} was used for a different request")
        return existing
    active = db.scalar(
        select(func.count()).select_from(ResearchJob).where(ResearchJob.status.in_(_ACTIVE))
    )
    if (active or 0) >= settings.RESEARCH_MAX_QUEUED_JOBS:
        raise QueueFull("research queue is full")
    job, created = create_job(
        db,
        ResearchJobIn(
            owner_key=owner_key,
            job_type=_job_type(request),
            idempotency_key=idempotency_key,
            request=request.model_dump(mode="json"),
            event_id=request.event_ids[0] if request.event_ids else None,
        ),
    )
    if not created:
        return job
    job.status = ResearchJobStatus.PUBLISH_PENDING.value
    job.correlation_id = correlation_id[:128]
    enqueue(
        db,
        topic=settings.KAFKA_TOPIC_RESEARCH_REQUEST,
        kind="request",
        payload=ResearchPayload(
            kind="request",
            research_id=str(job.id),
            idempotency_key=idempotency_key,
            request_version=settings.RESEARCH_REQUEST_VERSION,
        ),
        correlation_id=correlation_id,
        external_id=str(job.id),
        research_job_id=job.id,
    )
    db.flush()
    return job


def research_view(db: Session, research_id: uuid.UUID) -> Optional[dict]:
    job = db.get(ResearchJob, research_id)
    if job is None:
        return None
    report = db.scalar(
        select(Report).where(Report.research_job_id == job.id).order_by(Report.version.desc())
    )
    plan = None
    body = None
    limitations = []
    if report is not None:
        for section in report.sections or []:
            if section.get("section") == "plan":
                plan = section.get("body")
            elif section.get("section") == "report":
                body = section.get("body")
                limitations = list((body or {}).get("limitations") or [])
    claims = db.scalars(select(ResearchClaim).where(ResearchClaim.research_job_id == job.id)).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.research_job_id == job.id)).all()
    return {
        "research_id": str(job.id),
        "status": job.status,
        "correlation_id": job.correlation_id,
        "request": job.request,
        "plan": plan,
        "report": body,
        "limitations": limitations,
        "claims": [
            {
                "statement": row.statement,
                "claim_kind": row.claim_kind,
                "verification_status": row.verification_status,
            }
            for row in claims
        ],
        "verification_summary": _summary(claims),
        "agent_runs": [
            {
                "agent": row.agent_name,
                "status": row.status,
                "prompt_version": row.prompt_version,
                "attempt": row.attempt,
                "error": sanitize(row.error or "") if row.error else None,
            }
            for row in runs
        ],
        "error": sanitize(job.error) if job.error else None,
        "error_category": job.error_category,
        "attempts": job.attempts,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _summary(claims) -> dict:
    counts = {"supported": 0, "uncertain": 0, "unsupported": 0, "conflicting": 0}
    for row in claims:
        if row.verification_status in counts:
            counts[row.verification_status] += 1
    return counts


def outbox_for(db: Session, job_id: uuid.UUID) -> list:
    return list(db.scalars(select(ResearchOutbox).where(ResearchOutbox.research_job_id == job_id)))

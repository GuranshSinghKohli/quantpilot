"""Optional persistence onto existing research_jobs / agent_runs / research_claims."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import AgentRunStatus, ClaimKind, EvidenceStance, ResearchJobStatus, ResearchJobType, VerificationStatus
from app.domain.enums import ReportStatus
from app.domain.models import AgentRun, ClaimEvidence, EvidenceRecord, Report, ResearchClaim, ResearchJob
from app.domain.schemas import ResearchJobIn
from app.repositories.research_jobs import create_job
from app.research.schemas import ResearchResult
from app.retrieval.package import ResearchRequest


def persist_result(
    db: Session,
    request: ResearchRequest,
    result: ResearchResult,
    *,
    owner_key: str = "research-api",
    idempotency_key: Optional[str] = None,
    job: Optional[ResearchJob] = None,
) -> uuid.UUID:
    payload = request.model_dump(mode="json")
    owns_job = job is None
    if owns_job:
        key = idempotency_key or hashlib.sha256(f"{owner_key}|{uuid.uuid4().hex}".encode("utf-8")).hexdigest()
        job, created = create_job(
            db,
            ResearchJobIn(
                owner_key=owner_key,
                job_type=_job_type(request),
                idempotency_key=key,
                request=payload,
                event_id=request.event_ids[0] if request.event_ids else None,
            ),
        )
    else:
        created = False
    now = datetime.now(timezone.utc)
    existing_claims = db.scalar(
        select(func.count()).select_from(ResearchClaim).where(ResearchClaim.research_job_id == job.id)
    )
    _apply_job_status(job, result, now, bump_attempts=owns_job)
    if existing_claims:
        result.research_job_id = job.id
        result.research_id = job.id
        return job.id

    run_ids: Dict[str, uuid.UUID] = {}
    for meta in result.agent_metadata:
        status = {
            "succeeded": AgentRunStatus.SUCCEEDED.value,
            "failed": AgentRunStatus.FAILED.value,
            "skipped": AgentRunStatus.SKIPPED.value,
        }.get(meta.status, AgentRunStatus.SUCCEEDED.value)
        run = AgentRun(
            research_job_id=job.id,
            agent_name=meta.agent,
            stage=meta.agent,
            attempt=max(meta.retry_count, 0) + 1,
            status=status,
            model=meta.model,
            prompt_version=meta.prompt_version,
            input_hash=hashlib.sha256(
                f"{meta.agent}|{meta.prompt_version}|{meta.status}".encode("utf-8")
            ).hexdigest(),
            tokens_in=meta.input_tokens,
            tokens_out=meta.output_tokens,
            latency_ms=meta.duration_ms,
            error=meta.error_type,
            started_at=meta.started_at,
            finished_at=meta.completed_at,
        )
        db.add(run)
        db.flush()
        run_ids[meta.agent] = run.id

    seen_statements = set()
    for claim in [*result.verified_claims, *result.unsupported_claims, *result.conflicts]:
        if claim.statement in seen_statements:
            continue
        seen_statements.add(claim.statement)
        row = ResearchClaim(
            research_job_id=job.id,
            agent_run_id=run_ids.get(claim.agent),
            statement=claim.statement,
            claim_kind=claim.claim_type.value if isinstance(claim.claim_type, ClaimKind) else str(claim.claim_type),
            verification_status=claim.verification_status.value
            if isinstance(claim.verification_status, VerificationStatus)
            else str(claim.verification_status),
            confidence=claim.confidence,
            uncertainty_note="; ".join(claim.issues) if claim.issues else None,
        )
        db.add(row)
        db.flush()
        for ref in claim.evidence_refs:
            evidence_id = _as_uuid(ref)
            if evidence_id is None or db.get(EvidenceRecord, evidence_id) is None:
                continue
            db.add(
                ClaimEvidence(
                    claim_id=row.id,
                    evidence_id=evidence_id,
                    stance=EvidenceStance.SUPPORTS.value
                    if claim.verification_status != VerificationStatus.CONFLICTING
                    else EvidenceStance.CONTRADICTS.value,
                )
            )
    if result.report is not None:
        db.add(
            Report(
                research_job_id=job.id,
                version=1,
                status=ReportStatus.FINAL.value,
                title=result.report.title[:512],
                sections=[
                    {"section": "plan", "body": result.plan.model_dump(mode="json") if result.plan else {}},
                    {"section": "report", "body": result.report.model_dump(mode="json")},
                ],
            )
        )
    result.research_job_id = job.id
    result.research_id = job.id
    return job.id


def _apply_job_status(job: ResearchJob, result: ResearchResult, now: datetime, *, bump_attempts: bool) -> None:
    if result.status == "failed":
        job.status = ResearchJobStatus.FAILED.value
    elif result.status == "partial":
        job.status = ResearchJobStatus.PARTIAL.value
    else:
        job.status = ResearchJobStatus.SUCCEEDED.value
    job.current_stage = "finalize_result"
    job.started_at = job.started_at or now
    job.finished_at = now
    job.worker_id = None
    job.lease_until = None
    if bump_attempts:
        job.attempts = (job.attempts or 0) + 1


def _job_type(request: ResearchRequest) -> ResearchJobType:
    if request.event_ids and not request.entity_ids:
        return ResearchJobType.EVENT_RESEARCH
    return ResearchJobType.COMPANY_RESEARCH


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None

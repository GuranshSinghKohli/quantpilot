"""Optional persistence onto existing research_jobs / agent_runs / research_claims."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.domain.enums import AgentRunStatus, ClaimKind, EvidenceStance, ResearchJobStatus, ResearchJobType, VerificationStatus
from app.domain.models import AgentRun, ClaimEvidence, EvidenceRecord, ResearchClaim
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
) -> uuid.UUID:
    payload = request.model_dump(mode="json")
    key = hashlib.sha256(f"{owner_key}|{uuid.uuid4().hex}".encode("utf-8")).hexdigest()
    job, _ = create_job(
        db,
        ResearchJobIn(
            owner_key=owner_key,
            job_type=_job_type(request),
            idempotency_key=key,
            request=payload,
            event_id=request.event_ids[0] if request.event_ids else None,
        ),
    )
    now = datetime.now(timezone.utc)
    failed = any(item.get("error_type") in {"invalid_request", "oversized", "auth"} for item in result.errors)
    partial = bool(result.unavailable_components) or any(item.get("stage") in {"news", "company", "technology", "market"} for item in result.errors)
    if failed and not result.findings and not result.verified_claims:
        job.status = ResearchJobStatus.FAILED.value
    elif partial:
        job.status = ResearchJobStatus.PARTIAL.value
    else:
        job.status = ResearchJobStatus.SUCCEEDED.value
    job.current_stage = "finalize_result"
    job.started_at = job.started_at or now
    job.finished_at = now
    job.attempts = (job.attempts or 0) + 1

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

    for claim in [*result.verified_claims, *result.unsupported_claims, *result.conflicts]:
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
    result.research_job_id = job.id
    return job.id


def _job_type(request: ResearchRequest) -> ResearchJobType:
    if request.event_ids and not request.entity_ids:
        return ResearchJobType.EVENT_RESEARCH
    return ResearchJobType.COMPANY_RESEARCH


def _as_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None

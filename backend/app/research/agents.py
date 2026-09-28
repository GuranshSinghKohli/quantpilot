"""Specialized research agents. They consume an EvidencePackage and approved tools."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.observability.logger import get_logger, log_event
from app.research.llm import FakeLLMProvider, LLMError, LLMProvider, get_llm_provider
from app.research.metrics import RESEARCH_METRICS
from app.research.prompts import PROMPT_VERSIONS, SYSTEM_PROMPTS, package_brief
from app.research.schemas import AgentFinding, AgentFindingsPayload, AgentRunMeta
from app.research.tools import invoke_tool
from app.research.verify import verify_findings
from app.retrieval.package import ResearchRequest

logger = get_logger("research")

SPECIALISTS = ("news", "company", "technology", "market")
_ADVICE_MARKETS = ("buy", "sell", "hold", "price target", "expected return")


def run_specialist(
    agent: str,
    package: Dict[str, Any],
    *,
    db: Optional[Session] = None,
    request: Optional[ResearchRequest] = None,
    provider: Optional[LLMProvider] = None,
    extra_tool: Optional[Callable[..., Any]] = None,
) -> Tuple[List[AgentFinding], AgentRunMeta, Optional[str]]:
    settings = get_settings()
    llm = provider or get_llm_provider()
    prompt_version = PROMPT_VERSIONS[agent]
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    retries = 0
    error_type = None
    status = "succeeded"
    findings: List[AgentFinding] = []
    tokens_in = tokens_out = 0
    cost = None
    model = getattr(llm, "model_name", settings.LLM_MODEL)
    model_version = getattr(llm, "model_version", settings.LLM_MODEL_VERSION)

    brief = package_brief(package, max_excerpt=settings.RETRIEVAL_MAX_EXCERPT)
    user = json.dumps({"agent": agent, "context": brief}, default=str)
    last_error: Optional[LLMError] = None
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            response = llm.generate_structured(
                system=SYSTEM_PROMPTS[agent],
                user=user,
                schema=AgentFindingsPayload,
                prompt_name=agent,
                prompt_version=prompt_version,
                context=brief,
            )
            payload = response.data
            if not isinstance(payload, AgentFindingsPayload):
                payload = AgentFindingsPayload.model_validate(payload)
            findings = [_bound_finding(item, agent) for item in payload.findings]
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = response.usage.estimated_cost
            model = response.model
            model_version = response.model_version
            last_error = None
            break
        except LLMError as exc:
            last_error = exc
            if exc.retryable and attempt < settings.LLM_MAX_RETRIES:
                retries += 1
                RESEARCH_METRICS.inc(
                    "agent_retries_total",
                    1,
                    operation="agent",
                    status="retry",
                    agent=agent,
                    error_type=exc.error_type,
                    component="research",
                )
                delay = min(0.01 * (2**attempt), 0.05) if isinstance(llm, FakeLLMProvider) else min(2**attempt, 8)
                time.sleep(delay)
                continue
            status = "failed"
            error_type = exc.error_type
            findings = []
            break

    if extra_tool and db is not None and findings:
        extra_tool(db, agent, request)

    if agent == "market":
        findings = [item for item in findings if not _has_advice(item.statement)]

    duration_ms = int((time.perf_counter() - t0) * 1000)
    completed = datetime.now(timezone.utc)
    RESEARCH_METRICS.inc(
        "agent_runs_total",
        1,
        operation="agent",
        status=status,
        agent=agent,
        model=model,
        component="research",
    )
    RESEARCH_METRICS.observe(
        "agent_duration_seconds",
        duration_ms / 1000.0,
        operation="agent",
        status=status,
        agent=agent,
        component="research",
    )
    RESEARCH_METRICS.inc(
        "agent_tokens_total",
        tokens_in + tokens_out,
        operation="agent",
        status=status,
        agent=agent,
        model=model,
        component="research",
    )
    if status == "failed":
        RESEARCH_METRICS.inc(
            "agent_failures_total",
            1,
            operation="agent",
            status="failed",
            agent=agent,
            error_type=error_type or "provider",
            component="research",
        )
    log_event(
        logger,
        20 if status == "succeeded" else 40,
        "RESEARCH_AGENT_FINISHED",
        extra_data={
            "component": "research",
            "operation": "agent",
            "agent": agent,
            "model": model,
            "prompt_version": prompt_version,
            "status": status,
            "duration_ms": duration_ms,
        },
    )
    meta = AgentRunMeta(
        agent=agent,
        model=model,
        model_version=model_version,
        prompt_version=prompt_version,
        started_at=started,
        completed_at=completed,
        duration_ms=duration_ms,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        total_tokens=tokens_in + tokens_out,
        estimated_cost=cost,
        retry_count=retries,
        status=status,
        error_type=error_type,
    )
    err = None if last_error is None else last_error.error_type
    return findings, meta, err


def run_evidence_agent(
    findings: List[AgentFinding],
    package: Dict[str, Any],
) -> Tuple[List[Any], AgentRunMeta]:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    claims = verify_findings(findings, package)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    for claim in claims:
        RESEARCH_METRICS.inc(
            "claim_verification_total",
            1,
            operation="verify",
            status="ok",
            verification_status=claim.verification_status.value,
            component="research",
        )
        if claim.verification_status.value == "unsupported":
            RESEARCH_METRICS.inc(
                "unsupported_claims_total",
                1,
                operation="verify",
                status="ok",
                verification_status="unsupported",
                component="research",
            )
        if claim.verification_status.value == "conflicting":
            RESEARCH_METRICS.inc(
                "conflicting_claims_total",
                1,
                operation="verify",
                status="ok",
                verification_status="conflicting",
                component="research",
            )
    RESEARCH_METRICS.inc(
        "agent_runs_total",
        1,
        operation="agent",
        status="succeeded",
        agent="evidence",
        component="research",
    )
    meta = AgentRunMeta(
        agent="evidence",
        model="deterministic-guard",
        model_version="v1",
        prompt_version=PROMPT_VERSIONS["evidence"],
        started_at=started,
        completed_at=datetime.now(timezone.utc),
        duration_ms=duration_ms,
        status="succeeded",
    )
    return claims, meta


def maybe_refresh_documents(
    db: Session,
    agent: str,
    request: Optional[ResearchRequest],
    package: Dict[str, Any],
    provider=None,
) -> None:
    """Optional tool use: news/company/technology may re-query documents. Bounded."""
    if request is None:
        return
    invoke_tool(
        "retrieve_documents",
        agent,
        db,
        request=request,
        query=request.query,
        entity_ids=request.entity_ids,
        event_ids=request.event_ids,
        package=package,
        provider=provider,
    )


def _bound_finding(item: AgentFinding, agent: str) -> AgentFinding:
    data = item.model_copy(deep=True)
    data.agent = agent
    data.citations = list(data.citations)[:12]
    data.evidence_refs = list(data.evidence_refs)[:12]
    data.statement = data.statement[:2000]
    return data


def _has_advice(statement: str) -> bool:
    lowered = statement.lower()
    return any(token in lowered for token in _ADVICE_MARKETS)

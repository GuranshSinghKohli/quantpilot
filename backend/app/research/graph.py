"""Fixed-topology LangGraph. The LLM cannot invent nodes or edges."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from app.core.settings import get_settings
from app.observability.logger import get_logger, log_event
from app.research.agents import SPECIALISTS, run_evidence_agent, run_specialist
from app.research.llm import LLMError, get_llm_provider
from app.research.metrics import RESEARCH_METRICS
from app.research.plan import accept_plan, bounded_plan_context, fallback_plan
from app.research.prompts import PROMPT_VERSIONS, SYSTEM_PROMPTS
from app.research.report import assemble_report
from app.research.runtime import research_db, research_embed, research_llm
from app.research.schemas import (
    AgentFinding,
    AgentRunMeta,
    MasterPlanPayload,
    ResearchPlan,
    ResearchReport,
    ResearchResult,
    SummaryDraft,
    VerifiedClaim,
    initial_state,
)
from app.research.verify import used_citations
from app.retrieval.package import RequestOversized, ResearchRequest, assemble_package

logger = get_logger("research")


class ResearchState(TypedDict, total=False):
    request: Dict[str, Any]
    evidence_package: Dict[str, Any]
    agent_findings: Dict[str, List[Dict[str, Any]]]
    claims: List[Dict[str, Any]]
    verification_results: List[Dict[str, Any]]
    errors: List[Dict[str, str]]
    metadata: Dict[str, Any]
    plan: Dict[str, Any]
    report: Dict[str, Any]
    result: Dict[str, Any]


def validate_request_node(state: ResearchState) -> Dict[str, Any]:
    errors = list(state.get("errors") or [])
    try:
        ResearchRequest.model_validate(state["request"])
    except Exception as exc:
        errors.append({"stage": "validate_request", "error_type": "invalid_request", "detail": str(exc)[:200]})
    return {"errors": errors}


def build_evidence_package_node(state: ResearchState) -> Dict[str, Any]:
    if any(item.get("error_type") == "invalid_request" for item in state.get("errors") or []):
        return {}
    db = research_db.get()
    if db is None:
        errors = list(state.get("errors") or [])
        errors.append({"stage": "build_evidence_package", "error_type": "missing_db"})
        return {"errors": errors}
    request = ResearchRequest.model_validate(state["request"])
    try:
        package = assemble_package(db, request, provider=research_embed.get())
    except RequestOversized as exc:
        errors = list(state.get("errors") or [])
        errors.append({"stage": "build_evidence_package", "error_type": "oversized", "detail": str(exc)})
        return {"errors": errors}
    metadata = dict(state.get("metadata") or {})
    if not (package.get("retrieval_metadata") or {}).get("semantic_available", True):
        metadata.setdefault("unavailable", []).append("semantic")
    return {"evidence_package": package, "metadata": metadata}


def _blocked(state: ResearchState) -> bool:
    return any(item.get("error_type") in {"invalid_request", "oversized"} for item in state.get("errors") or [])


def create_research_plan_node(state: ResearchState) -> Dict[str, Any]:
    if _blocked(state):
        return {}
    request = state.get("request") or {}
    package = state.get("evidence_package") or {}
    metadata = dict(state.get("metadata") or {})
    runs = list(metadata.get("agent_runs") or [])
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    context = bounded_plan_context(request, package)
    plan = None
    error_type = None
    try:
        provider = research_llm.get() or get_llm_provider()
        response = provider.generate_structured(
            system=SYSTEM_PROMPTS["master"],
            user="plan",
            schema=MasterPlanPayload,
            prompt_name="master",
            prompt_version=PROMPT_VERSIONS["master"],
            context=context,
        )
        plan = accept_plan(response.data if isinstance(response.data, MasterPlanPayload) else MasterPlanPayload.model_validate(response.data), request, package)
        model = response.model
        model_version = response.model_version
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except LLMError as exc:
        plan = None
        error_type = exc.error_type
        model = "unavailable"
        model_version = "v1"
        tokens_in = tokens_out = 0
    if plan is None:
        RESEARCH_METRICS.inc(
            "research_plan_failures_total",
            1,
            operation="plan",
            status="fallback",
            component="research",
            error_type=error_type or "invalid_plan",
        )
        RESEARCH_METRICS.inc(
            "research_plan_rejected_total",
            1,
            operation="plan",
            status="rejected",
            source="llm",
            component="research",
        )
        plan = fallback_plan(request, package, note="planner output rejected; documented router used")
        status = "fallback"
    else:
        status = "succeeded"
    RESEARCH_METRICS.inc(
        "research_plans_total",
        1,
        operation="plan",
        status=status,
        source=plan.source,
        component="research",
    )
    if status == "fallback":
        RESEARCH_METRICS.inc(
            "research_plan_fallback_total",
            1,
            operation="plan",
            status="fallback",
            source="fallback",
            component="research",
        )
    for agent_name in plan.selected_agents:
        RESEARCH_METRICS.inc(
            "research_plan_agent_selected_total",
            1,
            agent=agent_name,
            source=plan.source,
            status=status,
            component="research",
        )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    RESEARCH_METRICS.observe(
        "research_duration_seconds",
        duration_ms / 1000.0,
        operation="plan",
        status=status,
        component="research",
    )
    runs.append(
        AgentRunMeta(
            agent="master",
            model=model,
            model_version=model_version,
            prompt_version=PROMPT_VERSIONS["master"],
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            total_tokens=tokens_in + tokens_out,
            status="succeeded",
            error_type=None if status == "succeeded" else (error_type or "invalid_plan"),
        ).model_dump(mode="json")
    )
    metadata["agent_runs"] = runs
    metadata["plan_source"] = plan.source
    return {"plan": plan.model_dump(mode="json"), "metadata": metadata}


def run_relevant_specialists_node(state: ResearchState) -> Dict[str, Any]:
    if _blocked(state):
        return {}
    package = state.get("evidence_package") or {}
    request = ResearchRequest.model_validate(state["request"])
    selected = list((state.get("plan") or {}).get("selected_agents") or [])
    llm = research_llm.get()
    findings: Dict[str, List[Dict[str, Any]]] = {}
    metas: List[Dict[str, Any]] = list((state.get("metadata") or {}).get("agent_runs") or [])
    errors = list(state.get("errors") or [])
    unavailable = list((state.get("metadata") or {}).get("unavailable") or [])
    try:
        provider = llm or get_llm_provider()
    except LLMError as exc:
        errors.append({"stage": "llm", "error_type": exc.error_type, "detail": str(exc)[:200]})
        metadata = dict(state.get("metadata") or {})
        metadata["unavailable"] = list(dict.fromkeys([*unavailable, "llm"]))
        metadata["agent_runs"] = metas
        return {"errors": errors, "metadata": metadata, "agent_findings": {}}

    now = datetime.now(timezone.utc)
    specialist_started = time.perf_counter()
    for agent in SPECIALISTS:
        if agent not in selected:
            metas.append(
                AgentRunMeta(
                    agent=agent,
                    model="not-run",
                    model_version="v1",
                    prompt_version=PROMPT_VERSIONS[agent],
                    started_at=now,
                    completed_at=now,
                    duration_ms=0,
                    status="skipped",
                ).model_dump(mode="json")
            )
            findings[agent] = []
            continue
        items, meta, err = run_specialist(
            agent,
            package,
            db=research_db.get(),
            request=request,
            provider=provider,
        )
        findings[agent] = [item.model_dump(mode="json") for item in items]
        metas.append(meta.model_dump(mode="json"))
        if err:
            errors.append({"stage": agent, "error_type": err})
            unavailable.append(agent)
            RESEARCH_METRICS.inc(
                "research_agent_failures_total",
                1,
                operation="agent",
                status="failed",
                agent=agent,
                error_type=err,
                component="research",
            )
    RESEARCH_METRICS.observe(
        "research_duration_seconds",
        time.perf_counter() - specialist_started,
        operation="specialist",
        status="partial" if unavailable else "ok",
        component="research",
    )
    metadata = dict(state.get("metadata") or {})
    metadata["agent_runs"] = metas
    metadata["unavailable"] = list(dict.fromkeys(unavailable))
    return {"agent_findings": findings, "errors": errors, "metadata": metadata}


def collect_findings_node(state: ResearchState) -> Dict[str, Any]:
    collected: List[Dict[str, Any]] = []
    for agent in SPECIALISTS:
        for row in (state.get("agent_findings") or {}).get(agent) or []:
            collected.append(row)
    return {"metadata": {**dict(state.get("metadata") or {}), "collected_count": len(collected)}}


def verify_claims_node(state: ResearchState) -> Dict[str, Any]:
    package = state.get("evidence_package") or {}
    collected: List[AgentFinding] = []
    for agent in SPECIALISTS:
        for row in (state.get("agent_findings") or {}).get(agent) or []:
            collected.append(AgentFinding.model_validate(row))
    t0 = time.perf_counter()
    claims, meta = run_evidence_agent(collected, package)
    RESEARCH_METRICS.observe(
        "research_duration_seconds",
        time.perf_counter() - t0,
        operation="verify",
        status="ok",
        component="research",
    )
    metadata = dict(state.get("metadata") or {})
    runs = list(metadata.get("agent_runs") or [])
    runs.append(meta.model_dump(mode="json"))
    metadata["agent_runs"] = runs
    dumped = [claim.model_dump(mode="json") for claim in claims]
    return {"claims": dumped, "verification_results": dumped, "metadata": metadata}


def resolve_conflicts_node(state: ResearchState) -> Dict[str, Any]:
    """Keep both sides. Do not pick a winner and do not rank sources."""
    claims = [VerifiedClaim.model_validate(row) for row in state.get("claims") or []]
    conflicts = [c for c in claims if c.verification_status.value == "conflicting"]
    metadata = dict(state.get("metadata") or {})
    if conflicts:
        metadata["conflict_note"] = "Sources disagree. Both citations are retained."
        RESEARCH_METRICS.inc(
            "research_verification_conflicts_total",
            len(conflicts),
            operation="conflict",
            status="conflicting",
            verification_status="conflicting",
            component="research",
        )
    unsupported = [c for c in claims if c.verification_status.value == "unsupported"]
    if unsupported:
        RESEARCH_METRICS.inc(
            "research_unsupported_claims_total",
            len(unsupported),
            operation="verify",
            status="unsupported",
            verification_status="unsupported",
            component="research",
        )
    return {"metadata": metadata}


def assemble_report_node(state: ResearchState) -> Dict[str, Any]:
    if _blocked(state):
        return {}
    claims = [VerifiedClaim.model_validate(row) for row in state.get("claims") or []]
    package = state.get("evidence_package") or {}
    query = str((state.get("request") or {}).get("query") or "")
    allowed = [
        c.statement
        for c in claims
        if c.verification_status.value == "supported" and c.citation_ids
    ]
    bullets = None
    t0 = time.perf_counter()
    try:
        provider = research_llm.get() or get_llm_provider()
        response = provider.generate_structured(
            system=SYSTEM_PROMPTS["report"],
            user="summary",
            schema=SummaryDraft,
            prompt_name="report",
            prompt_version=PROMPT_VERSIONS["report"],
            context={"allowed_statements": allowed[:8]},
        )
        draft = response.data if isinstance(response.data, SummaryDraft) else SummaryDraft.model_validate(response.data)
        bullets = draft.bullets
    except LLMError:
        bullets = None
    limitations = []
    for name in state.get("unavailable_components") or (state.get("metadata") or {}).get("unavailable") or []:
        limitations.append(f"{name} unavailable")
    report, dropped = assemble_report(
        query=query,
        claims=claims,
        package=package,
        summary_bullets=bullets,
        limitations=limitations,
    )
    if dropped:
        RESEARCH_METRICS.inc(
            "research_report_validation_failures_total",
            dropped,
            operation="report",
            status="rejected",
            component="research",
        )
    RESEARCH_METRICS.observe(
        "research_duration_seconds",
        time.perf_counter() - t0,
        operation="report",
        status="ok",
        component="research",
    )
    return {"report": report.model_dump(mode="json")}


def finalize_result_node(state: ResearchState) -> Dict[str, Any]:
    package = state.get("evidence_package") or {}
    claims = [VerifiedClaim.model_validate(row) for row in state.get("claims") or []]
    claims = claims[: get_settings().RESEARCH_MAX_CLAIMS]
    by_finding = {claim.finding_id: claim for claim in claims}
    visible: List[AgentFinding] = []
    for agent in SPECIALISTS:
        for row in (state.get("agent_findings") or {}).get(agent) or []:
            claim = by_finding.get(row["finding_id"])
            if claim is None or claim.verification_status.value == "unsupported":
                continue
            finding = AgentFinding.model_validate(row)
            finding.verification_status = claim.verification_status
            visible.append(finding)
    verified = [c for c in claims if c.verification_status.value == "supported"]
    unsupported = [c for c in claims if c.verification_status.value == "unsupported"]
    conflicts = [c for c in claims if c.verification_status.value == "conflicting"]
    uncertain = [c for c in claims if c.verification_status.value == "uncertain"]
    errors = list(state.get("errors") or [])
    unavailable = list((state.get("metadata") or {}).get("unavailable") or [])
    if any(item.get("error_type") in {"invalid_request", "oversized"} for item in errors):
        status = "failed"
    elif unavailable or any(item.get("stage") in SPECIALISTS for item in errors):
        status = "partial"
    else:
        status = "succeeded"
    plan_raw = state.get("plan") or {}
    report_raw = state.get("report") or {}
    result = ResearchResult(
        request=state.get("request") or {},
        findings=visible,
        verified_claims=verified + uncertain,
        unsupported_claims=unsupported,
        conflicts=conflicts,
        citations=used_citations(claims, package),
        agent_metadata=[AgentRunMeta.model_validate(row) for row in (state.get("metadata") or {}).get("agent_runs") or []],
        verification_summary={
            "supported": len(verified),
            "uncertain": len(uncertain),
            "unsupported": len(unsupported),
            "conflicting": len(conflicts),
            "findings": len(visible),
        },
        errors=errors,
        unavailable_components=unavailable,
        status=status,
        plan=ResearchPlan.model_validate(plan_raw) if plan_raw.get("objective") else None,
        report=ResearchReport.model_validate(report_raw) if report_raw.get("title") else None,
    )
    log_event(
        logger,
        20,
        "RESEARCH_RESULT_FINALIZED",
        extra_data={"component": "research", "operation": "finalize", "status": "ok"},
    )
    return {"result": result.model_dump(mode="json")}


def _build_graph():
    workflow = StateGraph(ResearchState)
    workflow.add_node("validate_request", validate_request_node)
    workflow.add_node("build_evidence_package", build_evidence_package_node)
    workflow.add_node("create_research_plan", create_research_plan_node)
    workflow.add_node("run_relevant_specialists", run_relevant_specialists_node)
    workflow.add_node("collect_findings", collect_findings_node)
    workflow.add_node("verify_claims", verify_claims_node)
    workflow.add_node("resolve_conflicts", resolve_conflicts_node)
    workflow.add_node("assemble_report", assemble_report_node)
    workflow.add_node("finalize_result", finalize_result_node)
    workflow.set_entry_point("validate_request")
    workflow.add_edge("validate_request", "build_evidence_package")
    workflow.add_edge("build_evidence_package", "create_research_plan")
    workflow.add_edge("create_research_plan", "run_relevant_specialists")
    workflow.add_edge("run_relevant_specialists", "collect_findings")
    workflow.add_edge("collect_findings", "verify_claims")
    workflow.add_edge("verify_claims", "resolve_conflicts")
    workflow.add_edge("resolve_conflicts", "assemble_report")
    workflow.add_edge("assemble_report", "finalize_result")
    workflow.add_edge("finalize_result", END)
    return workflow.compile()


_graph = None


def get_research_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def reset_research_graph() -> None:
    global _graph
    _graph = None


def invoke_research(request: Dict[str, Any]) -> Dict[str, Any]:
    state = initial_state(request)
    return get_research_graph().invoke(state)

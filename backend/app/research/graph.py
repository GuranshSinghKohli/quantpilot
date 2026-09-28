"""Fixed-topology LangGraph. The LLM cannot invent nodes or edges."""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from app.observability.logger import get_logger, log_event
from app.research.agents import SPECIALISTS, run_evidence_agent, run_specialist
from app.research.llm import LLMError, get_llm_provider
from app.research.runtime import research_db, research_embed, research_llm
from app.research.schemas import AgentFinding, AgentRunMeta, ResearchResult, VerifiedClaim, initial_state
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


def run_specialized_agents_node(state: ResearchState) -> Dict[str, Any]:
    if any(item.get("error_type") in {"invalid_request", "oversized"} for item in state.get("errors") or []):
        return {}
    package = state.get("evidence_package") or {}
    request = ResearchRequest.model_validate(state["request"])
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

    for agent in SPECIALISTS:
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
    metadata = dict(state.get("metadata") or {})
    metadata["agent_runs"] = metas
    metadata["unavailable"] = list(dict.fromkeys(unavailable))
    return {"agent_findings": findings, "errors": errors, "metadata": metadata}


def evidence_verification_node(state: ResearchState) -> Dict[str, Any]:
    package = state.get("evidence_package") or {}
    collected: List[AgentFinding] = []
    for agent in SPECIALISTS:
        for row in (state.get("agent_findings") or {}).get(agent) or []:
            collected.append(AgentFinding.model_validate(row))
    claims, meta = run_evidence_agent(collected, package)
    metadata = dict(state.get("metadata") or {})
    runs = list(metadata.get("agent_runs") or [])
    runs.append(meta.model_dump(mode="json"))
    metadata["agent_runs"] = runs
    dumped = [claim.model_dump(mode="json") for claim in claims]
    return {"claims": dumped, "verification_results": dumped, "metadata": metadata}


def finalize_result_node(state: ResearchState) -> Dict[str, Any]:
    package = state.get("evidence_package") or {}
    claims = [VerifiedClaim.model_validate(row) for row in state.get("claims") or []]
    visible = [
        AgentFinding.model_validate(row)
        for agent in SPECIALISTS
        for row in (state.get("agent_findings") or {}).get(agent) or []
        if any(claim.finding_id == row["finding_id"] and claim.verification_status.value != "unsupported" for claim in claims)
    ]
    verified = [c for c in claims if c.verification_status.value == "supported"]
    unsupported = [c for c in claims if c.verification_status.value == "unsupported"]
    conflicts = [c for c in claims if c.verification_status.value == "conflicting"]
    uncertain = [c for c in claims if c.verification_status.value == "uncertain"]
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
        errors=list(state.get("errors") or []),
        unavailable_components=list((state.get("metadata") or {}).get("unavailable") or []),
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
    workflow.add_node("run_specialized_agents", run_specialized_agents_node)
    workflow.add_node("evidence_verification", evidence_verification_node)
    workflow.add_node("finalize_result", finalize_result_node)
    workflow.set_entry_point("validate_request")
    workflow.add_edge("validate_request", "build_evidence_package")
    workflow.add_edge("build_evidence_package", "run_specialized_agents")
    workflow.add_edge("run_specialized_agents", "evidence_verification")
    workflow.add_edge("evidence_verification", "finalize_result")
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

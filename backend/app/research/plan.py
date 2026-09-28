"""Bounded research plans. The planner cannot invent agents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.research.schemas import ALLOWED_SPECIALISTS, PLAN_VERSION, MasterPlanPayload, ResearchPlan

_NEWS = ("news", "article", "headline", "announced", "announcement", "reported")
_COMPANY = ("company", "partnership", "partner", "acquisition", "acquire", "acquired", "merger")
_TECH = ("technology", "product", "platform", "model", "chip", "gpu", "accelerator")
_MARKET = ("market", "price", "return", "impact", "volume", "volatility", "trading")

_SECTIONS = (
    "key_facts",
    "market_observations",
    "company_relationships",
    "technology_findings",
    "implications",
    "scenarios",
    "conflicts",
    "limitations",
)


def _blob(query: str, package: Dict[str, Any]) -> str:
    parts = [query.lower()]
    for doc in package.get("documents") or []:
        parts.append(str(doc.get("title") or "").lower())
        parts.append(str(doc.get("excerpt") or "").lower())
    for event in package.get("events") or []:
        parts.append(str(event.get("title") or "").lower())
        parts.append(str(event.get("event_type") or "").lower())
    for edge in package.get("graph_facts") or []:
        parts.append(str(edge.get("relationship") or "").lower())
    return " ".join(parts)


def select_agents(query: str, package: Dict[str, Any]) -> List[str]:
    """Documented keyword + package router. Not a semantic planner."""
    text = _blob(query, package)
    selected: List[str] = []
    if any(token in text for token in _NEWS) or package.get("documents") or package.get("events"):
        selected.append("news")
    if any(token in text for token in _COMPANY) or package.get("entities") or package.get("graph_facts"):
        selected.append("company")
    tech_entity = any(
        str(item.get("entity_type") or "") in {"technology", "product"} for item in package.get("entities") or []
    )
    tech_edge = any(
        str(edge.get("relationship") or "") in {"USES", "DEVELOPS", "DEPENDS_ON"}
        for edge in package.get("graph_facts") or []
    )
    if any(token in text for token in _TECH) or tech_entity or tech_edge:
        selected.append("technology")
    if any(token in text for token in _MARKET) or package.get("market_facts"):
        selected.append("market")
    ceiling = get_settings().RESEARCH_MAX_AGENTS
    return [name for name in ALLOWED_SPECIALISTS if name in selected][:ceiling]


def fallback_plan(request: Dict[str, Any], package: Dict[str, Any], *, note: str) -> ResearchPlan:
    query = str(request.get("query") or "").strip()
    return ResearchPlan(
        objective=query[:500] or "research request",
        normalized_request={
            "query": query,
            "entity_ids": list(request.get("entity_ids") or []),
            "event_ids": list(request.get("event_ids") or []),
        },
        selected_agents=select_agents(query, package),
        evidence_requirements=["citation_required_for_facts"],
        requested_sections=list(_SECTIONS[: get_settings().RESEARCH_MAX_REPORT_SECTIONS]),
        reasoning_notes=note[:500],
        plan_version=PLAN_VERSION,
        source="fallback",
    )


def accept_plan(
    payload: MasterPlanPayload,
    request: Dict[str, Any],
    package: Dict[str, Any],
) -> Optional[ResearchPlan]:
    """Return a plan or None when the model output is invalid or oversized."""
    settings = get_settings()
    names = list(payload.selected_agents)
    if len(names) > settings.RESEARCH_MAX_AGENTS:
        return None
    if any(name not in ALLOWED_SPECIALISTS for name in names):
        return None
    ordered = [name for name in ALLOWED_SPECIALISTS if name in names]
    sections = [item for item in payload.requested_sections if item in _SECTIONS]
    if len(payload.requested_sections) > settings.RESEARCH_MAX_REPORT_SECTIONS:
        return None
    query = str(request.get("query") or "").strip()
    return ResearchPlan(
        objective=payload.objective[:500],
        normalized_request={
            "query": query,
            "entity_ids": list(request.get("entity_ids") or []),
            "event_ids": list(request.get("event_ids") or []),
        },
        selected_agents=ordered,
        evidence_requirements=list(payload.evidence_requirements)[:8] or ["citation_required_for_facts"],
        requested_sections=sections or list(_SECTIONS[: settings.RESEARCH_MAX_REPORT_SECTIONS]),
        reasoning_notes=payload.reasoning_notes[:500],
        plan_version=PLAN_VERSION,
        source="llm",
    )


def bounded_plan_context(request: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    """What the planner is allowed to see. No credentials and no raw documents."""
    return {
        "query": str(request.get("query") or "")[:500],
        "entity_count": len(package.get("entities") or []),
        "event_count": len(package.get("events") or []),
        "document_count": len(package.get("documents") or []),
        "graph_count": len(package.get("graph_facts") or []),
        "market_count": len(package.get("market_facts") or []),
        "allowed_agents": list(ALLOWED_SPECIALISTS),
        "candidate_agents": select_agents(str(request.get("query") or ""), package),
        "semantic_available": (package.get("retrieval_metadata") or {}).get("semantic_available"),
    }

"""Bounded research plans. The planner cannot invent agents.

Routing reads the request, not the evidence text. A document that mentions a
GPU does not by itself select the technology agent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.core.settings import get_settings
from app.research.schemas import ALLOWED_SPECIALISTS, PLAN_VERSION, MasterPlanPayload, ResearchPlan

_NEWS = ("news", "article", "headline", "announced", "announcement", "reported", "publication", "conference")
_COMPANY = (
    "partnership", "partner", "acquisition", "acquire", "acquired",
    "merger", "customer", "supplier", "competitor", "ownership", "identity", "canonical", "relationship",
)
_TECH = (
    "technology", "product", "platform", "model", "framework", "chip", "gpu",
    "accelerator", "architecture", "release", "released",
)
_MARKET = ("stock", "price", "return", "returns", "market", "volume", "volatility", "benchmark", "trading")
_ACTION = ("partner", "partnership", "acquisition", "acquire", "acquired", "merger", "announced", "announcement")
_TECH_WITH_COMPANY = ("use", "uses", "using", "develop", "develops")

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


def _tokens(text: str) -> Set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# Verb stems only. Nouns such as "platform" stay exact so "Meta Platforms" is not a technology request.
_STEMS = ("partner", "acquire", "announce", "accelerat", "technolog", "return", "develop", "release")


def _has(tokens: Set[str], words: Sequence[str]) -> bool:
    vocab = set(words)
    for tok in tokens:
        if tok in vocab:
            return True
        for stem in _STEMS:
            if len(tok) > len(stem) and tok.startswith(stem) and any(word.startswith(stem) for word in vocab):
                return True
    return False


def route_query(query: str, package: Optional[Dict[str, Any]] = None) -> Tuple[List[str], List[str]]:
    """Classify the request. Package presence does not add an agent.

    ``package`` is accepted so callers can stay package-aware later. It is not
    scanned for keywords. Fixture-based routing, not planner intelligence.
    """
    del package
    tokens = _tokens(query)
    news = _has(tokens, _NEWS)
    company = _has(tokens, _COMPANY) or ("what" in tokens and "is" in tokens)
    tech = _has(tokens, _TECH)
    market = _has(tokens, _MARKET)
    if company and _has(tokens, _ACTION):
        news = True
    if tech and _has(tokens, _TECH_WITH_COMPANY):
        company = True
    if tech and _has(tokens, ("release", "released", "announced", "announcement")):
        news = True
    if market and _has(tokens, ("announcement", "announced", "news", "acquisition", "acquire", "partner", "partnership")):
        news = True
        if _has(tokens, ("acquisition", "acquire", "acquired", "partner", "partnership")):
            company = True
    if ("what" in tokens and "is" in tokens or _has(tokens, ("identity", "canonical"))) and not _has(
        tokens, _ACTION + ("news", "article", "headline")
    ):
        news = False
    reasons = []
    if news:
        reasons.append("News coverage requested.")
    if company:
        reasons.append("Company relationship requested.")
    if tech:
        reasons.append("Technology usage requested.")
    if market:
        reasons.append("Market movement requested.")
    flags = {"news": news, "company": company, "technology": tech, "market": market}
    ceiling = get_settings().RESEARCH_MAX_AGENTS
    selected = [name for name in ALLOWED_SPECIALISTS if flags[name]][:ceiling]
    return selected, reasons


def select_agents(query: str, package: Dict[str, Any]) -> List[str]:
    """Request-scoped router. Not a semantic planner."""
    selected, _reasons = route_query(query, package)
    return selected


def fallback_plan(request: Dict[str, Any], package: Dict[str, Any], *, note: str) -> ResearchPlan:
    query = str(request.get("query") or "").strip()
    selected, reasons = route_query(query, package)
    text = " ".join([note.strip(), *reasons]).strip()
    return ResearchPlan(
        objective=query[:500] or "research request",
        normalized_request={
            "query": query,
            "entity_ids": list(request.get("entity_ids") or []),
            "event_ids": list(request.get("event_ids") or []),
        },
        selected_agents=selected,
        evidence_requirements=["citation_required_for_facts"],
        requested_sections=list(_SECTIONS[: get_settings().RESEARCH_MAX_REPORT_SECTIONS]),
        reasoning_notes=text[:500],
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


# Fixture routing cases. required/optional/forbidden are research-objective labels,
# not a claim about planner intelligence.
ROUTING_CASES: List[Dict[str, Any]] = [
    {
        "request": "What did the latest announcement say about Company X?",
        "required_agents": ["news"],
        "optional_agents": [],
        "forbidden_agents": ["company", "technology", "market"],
    },
    {
        "request": "What companies has Company X partnered with?",
        "required_agents": ["company"],
        "optional_agents": ["news"],
        "forbidden_agents": ["technology", "market"],
    },
    {
        "request": "What technology does Company X use?",
        "required_agents": ["technology", "company"],
        "optional_agents": [],
        "forbidden_agents": ["news", "market"],
    },
    {
        "request": "How did Company X's announcement affect its stock?",
        "required_agents": ["news", "market"],
        "optional_agents": [],
        "forbidden_agents": ["company", "technology"],
    },
    {
        "request": "What happened to Company X's stock over the last 5 trading sessions?",
        "required_agents": ["market"],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "technology"],
    },
    {
        "request": "What technology did Company X release?",
        "required_agents": ["technology", "news"],
        "optional_agents": [],
        "forbidden_agents": ["company", "market"],
    },
    {
        "request": "Who acquired Company X?",
        "required_agents": ["company", "news"],
        "optional_agents": [],
        "forbidden_agents": ["technology", "market"],
    },
    {
        "request": "What was the market reaction to the acquisition?",
        "required_agents": ["company", "news", "market"],
        "optional_agents": [],
        "forbidden_agents": ["technology"],
    },
    {
        "request": "No evidence exists for this query.",
        "required_agents": [],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "technology", "market"],
    },
    {
        "request": "Tell me about the situation.",
        "required_agents": [],
        "optional_agents": [],
        "forbidden_agents": ["news", "company", "technology", "market"],
    },
]


def score_routing_examples() -> Dict[str, Any]:
    """Fixture-based routing correctness. Not human-judged planner quality."""
    required_hits = required_total = 0
    forbidden_hits = selected_total = unnecessary = 0
    optional_hits = optional_total = 0
    for case in ROUTING_CASES:
        selected = set(select_agents(str(case["request"]), {}))
        required = set(case["required_agents"])
        optional = set(case["optional_agents"])
        forbidden = set(case["forbidden_agents"])
        required_hits += len(selected & required)
        required_total += len(required)
        forbidden_hits += len(selected & forbidden)
        selected_total += len(selected)
        unnecessary += len(selected - required - optional)
        optional_hits += len(selected & optional)
        optional_total += len(optional)
    return {
        "label": "fixture-based routing correctness. Not planner intelligence.",
        "case_count": len(ROUTING_CASES),
        "required_agent_recall": round(required_hits / required_total, 4) if required_total else 1.0,
        "forbidden_agent_precision": round(1.0 - (forbidden_hits / selected_total), 4) if selected_total else 1.0,
        "unnecessary_agent_rate": round(unnecessary / selected_total, 4) if selected_total else 0.0,
        "optional_agent_usage": round(optional_hits / optional_total, 4) if optional_total else 0.0,
    }

"""Versioned specialist prompts. Bump the version string when wording changes."""

from __future__ import annotations

from typing import Any, Dict, List

PROMPT_VERSIONS = {
    "news": "news-v1",
    "company": "company-v1",
    "technology": "technology-v1",
    "market": "market-v1",
    "evidence": "evidence-v1",
    "master": "master-v1",
    "report": "report-v1",
}

_SHARED = """You are a QuantPilot research specialist. You receive a bounded EvidencePackage.
You may only use facts present in that package or returned by an approved tool.
Do not invent quotes, citations, URLs, numbers, partnerships, or acquisitions.
Every FACT and OBSERVATION needs at least one citation_id from the package.
Classify each finding as FACT, OBSERVATION, INFERENCE, or SCENARIO.
FACT = directly stated by a source or structured record.
OBSERVATION = a measured/structured value (returns, volume, recorded event).
INFERENCE = interpretation of supported facts. Never label inference as FACT.
SCENARIO = a conditional possibility. Never label a scenario as FACT.
Return JSON {"findings": [...]} only. finding_id must be stable for the same statement.
Do not produce buy, sell, hold, price targets, expected returns, or portfolio advice.
"""

SYSTEM_PROMPTS = {
    "master": (
        "Role: MasterResearch. You may only choose specialists from "
        "news, company, technology, market. You cannot add tools, nodes, or agents. "
        "Return JSON with objective, selected_agents, evidence_requirements, "
        "requested_sections, reasoning_notes. Do not invent evidence."
    ),
    "report": (
        "Role: report summary. Repeat only statements provided in allowed_statements. "
        "Do not add facts, recommendations, or causal claims. Return JSON {\"bullets\": [...]}."
    ),
    "news": _SHARED
    + "Role: NewsAgent. Summarize news documents and explicit events. "
    "Do not infer a relationship from two names appearing in one article.",
    "company": _SHARED
    + "Role: CompanyAgent. Use entity facts, evidenced graph relationships, and company events. "
    "Do not invent revenue, employees, valuation, market share, or unstated partnerships.",
    "technology": _SHARED
    + "Role: TechnologyAgent. Describe technologies as sources describe them. "
    "A mention is not USES. Only emit a company-technology relationship when a graph_fact or explicit excerpt supports it.",
    "market": _SHARED
    + "Role: MarketAgent. Report existing event-impact windows only. "
    "Say 'the stock returned X% over the +N trading-session window' and the benchmark return. "
    "Never claim the event caused the move. Never predict prices.",
    "evidence": _SHARED
    + "Role: EvidenceAgent. You do not create new facts. You only check citations against the package.",
}


def package_brief(package: Dict[str, Any], *, max_excerpt: int = 280) -> Dict[str, Any]:
    """Bounded view for prompts. IDs and short excerpts only — not raw documents."""
    citations = list(package.get("citations") or [])
    documents = []
    for doc in package.get("documents") or []:
        excerpt = str(doc.get("excerpt") or "")[:max_excerpt]
        documents.append(
            {
                "document_id": doc.get("document_id"),
                "chunk_id": doc.get("chunk_id"),
                "title": doc.get("title"),
                "published_at": doc.get("published_at"),
                "excerpt": excerpt,
                "citation_ids": _matching_citations(citations, document_id=doc.get("document_id")),
            }
        )
    return {
        "query": (package.get("request") or {}).get("query"),
        "entities": [
            {
                "entity_id": item.get("entity_id"),
                "name": item.get("canonical_name") or item.get("legal_name"),
                "ticker": item.get("ticker"),
                "entity_type": item.get("entity_type"),
                "citation_ids": _matching_citations(citations, title=item.get("canonical_name")),
            }
            for item in package.get("entities") or []
        ],
        "events": [
            {
                "event_id": item.get("event_id"),
                "type": item.get("event_type"),
                "title": item.get("title"),
                "occurred_at": item.get("occurred_at"),
                "citation_ids": _matching_citations(citations, locator_event=item.get("event_id")),
            }
            for item in package.get("events") or []
        ],
        "graph_facts": [
            {
                "from_entity": item.get("from_entity"),
                "relationship": item.get("relationship"),
                "to_entity": item.get("to_entity"),
                "citation_ids": _matching_citations(citations, evidence_id=item.get("evidence_id")),
            }
            for item in package.get("graph_facts") or []
        ],
        "market_facts": [
            {
                "event_id": item.get("event_id") or (item.get("source") or {}).get("event_id"),
                "symbol": item.get("symbol") or item.get("asset"),
                "windows": item.get("windows"),
                "volume": item.get("volume"),
                "volatility": item.get("volatility"),
                "calculation_version": item.get("calculation_version")
                or (item.get("source") or {}).get("calculation_version"),
                "citation_ids": _matching_citations(
                    citations, locator_event=(item.get("source") or {}).get("event_id")
                ),
            }
            for item in package.get("market_facts") or []
        ],
        "documents": documents,
        "citation_ids": [item.get("citation_id") for item in citations if item.get("citation_id")],
        "semantic_available": (package.get("retrieval_metadata") or {}).get("semantic_available"),
    }


def _matching_citations(
    citations: List[Dict[str, Any]],
    *,
    document_id: Any = None,
    evidence_id: Any = None,
    title: Any = None,
    locator_event: Any = None,
) -> List[str]:
    ids: List[str] = []
    for item in citations:
        cid = item.get("citation_id")
        if not cid:
            continue
        if document_id and str(item.get("document_id") or "") == str(document_id):
            ids.append(cid)
        elif evidence_id and str(item.get("evidence_id") or "") == str(evidence_id):
            ids.append(cid)
        elif title and item.get("title") == title:
            ids.append(cid)
        elif locator_event and str((item.get("locator") or {}).get("event_id") or "") == str(locator_event):
            ids.append(cid)
    return list(dict.fromkeys(ids))

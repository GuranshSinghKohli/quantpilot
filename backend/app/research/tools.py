"""Approved research tools. Wrappers over retrieval services — no SQL/Cypher."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.observability.logger import get_logger, log_event
from app.research.schemas import ToolResult
from app.retrieval.package import ResearchRequest, assemble_package
from app.retrieval.search import hit_payload, search
from app.retrieval.structured import load_entities, load_events, load_graph_facts, load_market_facts

logger = get_logger("research")

TOOL_PERMISSIONS: Dict[str, frozenset[str]] = {
    "news": frozenset({"retrieve_documents", "get_event", "verify_evidence"}),
    "company": frozenset({"get_entity", "get_entity_relationships", "get_event", "retrieve_documents"}),
    "technology": frozenset({"get_entity", "get_entity_relationships", "get_event", "retrieve_documents"}),
    "market": frozenset({"get_entity", "get_event", "get_market_impact", "verify_evidence"}),
    "evidence": frozenset({"verify_evidence", "retrieve_cited_evidence"}),
    "graph": frozenset({"retrieve_context"}),
}

_MAX_IDS = 20


def invoke_tool(
    name: str,
    agent: str,
    db: Session,
    *,
    package: Optional[Dict[str, Any]] = None,
    request: Optional[ResearchRequest] = None,
    entity_ids: Optional[Sequence[uuid.UUID]] = None,
    event_ids: Optional[Sequence[uuid.UUID]] = None,
    citation_ids: Optional[Sequence[str]] = None,
    query: Optional[str] = None,
    provider=None,
) -> ToolResult:
    allowed = TOOL_PERMISSIONS.get(agent, frozenset())
    if name not in allowed:
        log_event(
            logger,
            30,
            "RESEARCH_TOOL_DENIED",
            extra_data={"component": "research", "operation": "tool", "status": "denied", "agent": agent},
        )
        return ToolResult(errors=[f"tool_not_permitted:{name}"])
    ids = list(entity_ids or [])[:_MAX_IDS]
    ev_ids = list(event_ids or [])[:_MAX_IDS]
    try:
        if name == "retrieve_context":
            if request is None:
                return ToolResult(errors=["missing_request"])
            data = assemble_package(db, request, provider=provider)
            return _from_package(data)
        if name == "get_entity":
            rows = load_entities(db, ids, limit=_MAX_IDS)
            return _structured(rows, kind="entity")
        if name == "get_entity_relationships":
            rows = load_graph_facts(db, ids, limit=_MAX_IDS)
            return _structured(rows, kind="graph")
        if name == "get_event":
            rows = load_events(
                db,
                entity_ids=ids,
                event_ids=ev_ids,
                date_from=None,
                date_to=None,
                limit=_MAX_IDS,
            )
            return _structured(rows, kind="event")
        if name == "get_market_impact":
            rows = load_market_facts(db, ev_ids, limit=_MAX_IDS)
            return _structured(rows, kind="market")
        if name == "retrieve_documents":
            text = (query or (request.query if request else "") or "").strip()
            if not text:
                return ToolResult(errors=["missing_query"])
            result = search(
                db,
                text,
                entity_ids=ids or None,
                event_ids=ev_ids or None,
                top_k=10,
                provider=provider,
            )
            if not result.get("available"):
                return ToolResult(
                    data={"available": False, "hits": []},
                    errors=[str(result.get("reason") or "semantic_unavailable")],
                )
            hits = [hit_payload(hit) for hit in result.get("hits") or []]
            return ToolResult(
                data={"available": True, "hits": hits},
                citations=[{"citation_id": hit.get("chunk_id"), "document_id": hit.get("document_id")} for hit in hits],
                source_ids=[str(hit.get("source_id")) for hit in hits if hit.get("source_id")],
            )
        if name == "verify_evidence":
            return _lookup_citations(package or {}, citation_ids or [])
        if name == "retrieve_cited_evidence":
            return _lookup_citations(package or {}, citation_ids or [], include_items=True)
    except Exception as exc:
        log_event(
            logger,
            40,
            "RESEARCH_TOOL_FAILED",
            extra_data={"component": "research", "operation": "tool", "status": "error", "agent": agent},
        )
        return ToolResult(errors=[type(exc).__name__])
    return ToolResult(errors=[f"unknown_tool:{name}"])


def _from_package(package: Dict[str, Any]) -> ToolResult:
    citations = list(package.get("citations") or [])
    return ToolResult(
        data=package,
        citations=citations,
        source_ids=_ids(citations, "source_id"),
        evidence_ids=_ids(citations, "evidence_id"),
        errors=[]
        if (package.get("retrieval_metadata") or {}).get("semantic_available", True)
        else ["semantic_unavailable"],
    )


def _structured(rows: List[Dict[str, Any]], *, kind: str) -> ToolResult:
    citations = []
    source_ids = []
    evidence_ids = []
    kept = []
    for row in rows:
        source_id = row.get("source_id") or (row.get("source") or {}).get("source")
        evidence_id = row.get("evidence_id")
        record = (row.get("source") or {}).get("record") or row.get("event_id") or row.get("relationship_id")
        if kind == "entity" and not (row.get("source") or {}).get("record"):
            continue
        if kind == "graph" and (not row.get("source_id") or not row.get("evidence_id")):
            continue
        if kind == "event" and not row.get("source_id") and not row.get("evidence_id"):
            continue
        if kind == "market":
            src = row.get("source") or {}
            if not src.get("event_id") or not src.get("calculation_version"):
                continue
            record = src.get("event_id")
        kept.append(row)
        citations.append({"kind": kind, "record": record, "source_id": source_id, "evidence_id": evidence_id})
        if source_id:
            source_ids.append(str(source_id))
        if evidence_id:
            evidence_ids.append(str(evidence_id))
    if not kept and rows:
        return ToolResult(data=[], errors=["missing_provenance"])
    return ToolResult(data=kept, citations=citations, source_ids=source_ids, evidence_ids=evidence_ids)


def _lookup_citations(
    package: Dict[str, Any],
    citation_ids: Sequence[str],
    *,
    include_items: bool = False,
) -> ToolResult:
    index = {item.get("citation_id"): item for item in package.get("citations") or [] if item.get("citation_id")}
    found = []
    missing = []
    for cid in list(citation_ids)[:_MAX_IDS]:
        row = index.get(cid)
        if row is None:
            missing.append(cid)
        else:
            found.append(row)
    data: Any = found
    if include_items:
        data = {"citations": found, "documents": _docs_for(package, found)}
    errors = [f"unknown_citation:{cid}" for cid in missing]
    return ToolResult(
        data=data,
        citations=found,
        source_ids=_ids(found, "source_id"),
        evidence_ids=_ids(found, "evidence_id"),
        errors=errors,
    )


def _docs_for(package: Dict[str, Any], citations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wanted = {str(item.get("document_id")) for item in citations if item.get("document_id")}
    return [doc for doc in package.get("documents") or [] if str(doc.get("document_id")) in wanted]


def _ids(rows: Sequence[Dict[str, Any]], key: str) -> List[str]:
    return [str(row.get(key)) for row in rows if row.get(key)]


def parse_uuid_list(values: Optional[Sequence[Any]]) -> List[uuid.UUID]:
    out: List[uuid.UUID] = []
    for value in values or []:
        try:
            out.append(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except (ValueError, TypeError):
            continue
    return out[:_MAX_IDS]


def parse_optional_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

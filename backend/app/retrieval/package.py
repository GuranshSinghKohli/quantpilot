"""EvidencePackage assembly. Deterministic, bounded, not an agent."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.domain.models import EvidenceRecord
from app.market.binding import WINDOW_DEFINITION
from app.observability.logger import get_logger, log_event
from app.retrieval.metrics import RETRIEVAL_METRICS
from app.retrieval.search import hit_payload, search
from app.retrieval.structured import load_entities, load_events, load_graph_facts, load_market_facts

logger = get_logger("retrieval")

# Document, event, and graph citations come from a Source row. A listing or a
# calculated market window does not. URL coverage uses only the first group.
_URL_CITATION_KINDS = frozenset({"document_excerpt", "event_fact", "graph_fact"})


def http_source_url(value: Any) -> Optional[str]:
    """Return a stored http(s) URL. Anything else stays absent. Nothing is invented."""
    text = str(value or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return None


def citation_url_coverage(citations: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Share of URL-requiring citations that carry a real http(s) URL.

    Null when the package has no document, event, or graph citation. A null URL
    is not counted as covered, and this is not citation-object coverage.
    """
    requiring = [item for item in citations if item.get("kind") in _URL_CITATION_KINDS]
    if not requiring:
        return None
    valid = sum(1 for item in requiring if http_source_url(item.get("source_url")))
    return round(valid / len(requiring), 4)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    entity_ids: List[uuid.UUID] = Field(default_factory=list)
    event_ids: List[uuid.UUID] = Field(default_factory=list)
    date_from: Optional[datetime] = Field(default=None, description="Inclusive UTC bound.")
    date_to: Optional[datetime] = Field(default=None, description="Exclusive UTC bound. Half-open [date_from, date_to).")
    document_types: List[str] = Field(default_factory=list)
    max_documents: Optional[int] = None
    max_events: Optional[int] = None
    max_relationships: Optional[int] = None
    max_entities: Optional[int] = None
    top_k: Optional[int] = None

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        return value.strip()


class RequestOversized(ValueError):
    """Caller asked for more context than the configured ceiling."""


def _citation_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _limits(request: ResearchRequest) -> Dict[str, int]:
    settings = get_settings()
    mapping = {
        "entities": (request.max_entities, settings.RETRIEVAL_MAX_ENTITIES),
        "events": (request.max_events, settings.RETRIEVAL_MAX_EVENTS),
        "relationships": (request.max_relationships, settings.RETRIEVAL_MAX_RELATIONSHIPS),
        "documents": (request.max_documents, settings.RETRIEVAL_MAX_DOCUMENTS),
        "chunks": (request.top_k, settings.RETRIEVAL_MAX_CHUNKS),
    }
    out = {}
    for key, (asked, ceiling) in mapping.items():
        if asked is not None and asked > ceiling:
            raise RequestOversized(f"{key} limit {asked} exceeds ceiling {ceiling}")
        out[key] = min(asked or ceiling, ceiling)
    return out


def _cite_entity(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = item.get("source") or {}
    if not source.get("record"):
        return None
    return {
        "citation_id": _citation_id("entity", item["entity_id"]),
        "kind": "structured_fact",
        "source_id": None,
        "document_id": None,
        "chunk_id": None,
        "evidence_id": None,
        "source_url": None,
        "title": item.get("canonical_name"),
        "published_at": None,
        "locator": {"record": source.get("record"), "source": source.get("source")},
    }


def _cite_event(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not item.get("source_id") and not item.get("evidence_id"):
        return None
    return {
        "citation_id": _citation_id("event", item["event_id"]),
        "kind": "event_fact",
        "source_id": item.get("source_id"),
        "document_id": None,
        "chunk_id": None,
        "evidence_id": item.get("evidence_id"),
        "source_url": http_source_url(item.get("source_url")),
        "title": item.get("title"),
        "publisher": item.get("publisher"),
        "published_at": item.get("published_at"),
        "locator": {"event_id": item["event_id"]},
    }


def _cite_graph(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not item.get("source_id") or not item.get("evidence_id"):
        return None
    return {
        "citation_id": _citation_id("graph", item["relationship_id"], item["evidence_id"]),
        "kind": "graph_fact",
        "source_id": item["source_id"],
        "document_id": None,
        "chunk_id": None,
        "evidence_id": item["evidence_id"],
        "source_url": http_source_url(item.get("source_url")),
        "title": item["relationship"],
        "publisher": item.get("publisher"),
        "published_at": None,
        "locator": {"relationship_id": item["relationship_id"]},
    }


def _cite_market(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = item.get("source") or {}
    if not source.get("event_id") or not source.get("calculation_version"):
        return None
    return {
        "citation_id": _citation_id("market", source["event_id"], item.get("symbol", ""), source["calculation_version"]),
        "kind": "market_fact",
        "source_id": None,
        "document_id": None,
        "chunk_id": None,
        "evidence_id": None,
        "source_url": None,
        "title": f"{item.get('symbol') or (item.get('asset') or {}).get('symbol')} {source['calculation_version']}",
        "published_at": None,
        "locator": source,
        "market": _market_identity(item, source),
    }


def _cite_document(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not item.get("source_id") or not item.get("document_id") or not item.get("chunk_id"):
        return None
    return {
        "citation_id": _citation_id("doc", item["document_id"], item["chunk_id"]),
        "kind": "document_excerpt",
        "source_id": item["source_id"],
        "document_id": item["document_id"],
        "chunk_id": item["chunk_id"],
        "evidence_id": None,
        "source_url": http_source_url(item.get("source_url")),
        "title": item.get("title"),
        "publisher": item.get("publisher"),
        "published_at": item.get("published_at"),
        "locator": item.get("locator") or {},
    }


def _market_identity(item: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    symbol = item.get("symbol") or (item.get("asset") or {}).get("symbol")
    entity_id = item.get("entity_id")
    verified = bool(symbol and entity_id and item.get("identity_verified") is True)
    return {
        "entity_id": entity_id,
        "symbol": symbol,
        "canonical_name": item.get("canonical_name"),
        "exchange": item.get("exchange"),
        "security_type": item.get("security_type") or ("equity" if verified else None),
        "configured_benchmark_symbol": item.get("benchmark_symbol") or get_settings().MARKET_BENCHMARK_SYMBOL,
        "event_id": source.get("event_id"),
        "calculation_version": source.get("calculation_version"),
        "window_definition": item.get("window_definition") or WINDOW_DEFINITION,
        "identity_verified": verified,
        "event_trading_date": item.get("event_trading_date"),
        "windows": item.get("windows") or {},
        "volume": item.get("volume") or {},
        "volatility": item.get("volatility") or {},
        "method": "session return relative to the configured benchmark; not a causal effect",
    }


def _bind_document_evidence(
    db: Session,
    documents: Sequence[Dict[str, Any]],
    citations: List[Dict[str, Any]],
) -> None:
    """Attach an existing evidence record. Never invent an id.

    A record counts only when its source is the citation's source, its
    document id agrees when one is stored, and its excerpt is in the chunk.
    """
    by_chunk = {str(item.get("chunk_id") or ""): item for item in documents}
    source_ids = []
    for item in documents:
        try:
            source_ids.append(uuid.UUID(str(item.get("source_id"))))
        except (TypeError, ValueError):
            continue
    records: List[EvidenceRecord] = []
    if source_ids:
        records = list(
            db.scalars(select(EvidenceRecord).where(EvidenceRecord.source_id.in_(source_ids)))
        )
    grouped: Dict[str, List[EvidenceRecord]] = {}
    for record in records:
        grouped.setdefault(str(record.source_id), []).append(record)
    for citation in citations:
        if citation.get("kind") != "document_excerpt":
            continue
        document = by_chunk.get(str(citation.get("chunk_id") or ""))
        if document is None:
            continue
        entity_ids = [str(item) for item in (document.get("entity_ids") or []) if item]
        citation["entity_ids"] = entity_ids
        match = _matching_evidence(grouped.get(str(citation.get("source_id") or ""), []), document)
        if match is None:
            citation["evidence_id"] = None
            citation.pop("evidence", None)
            continue
        citation["evidence_id"] = str(match.id)
        citation["evidence"] = {
            "evidence_id": str(match.id),
            "source_id": str(match.source_id),
            "document_id": str(match.document_id) if match.document_id else None,
            "entity_ids": entity_ids,
        }
        if not citation.get("source_url"):
            citation["source_url"] = http_source_url(document.get("source_url"))


def _matching_evidence(
    records: Sequence[EvidenceRecord],
    document: Dict[str, Any],
) -> Optional[EvidenceRecord]:
    haystack = f"{document.get('title') or ''}\n{document.get('excerpt') or ''}".lower()
    document_id = str(document.get("document_id") or "")
    matches = []
    for record in records:
        if record.document_id is not None and document_id and str(record.document_id) != document_id:
            continue
        excerpt = " ".join(str(record.excerpt or "").split()).lower()
        if excerpt and excerpt in " ".join(haystack.split()):
            matches.append(record)
    if not matches:
        return None
    matches.sort(key=lambda item: (item.document_id is not None, len(item.excerpt or "")), reverse=True)
    return matches[0]


def assemble_package(db: Session, request: ResearchRequest, *, provider=None) -> Dict[str, Any]:
    # Imported here so retrieval can load without initializing the research package.
    # app.research.__init__ imports the agent graph, which imports this module.
    from app.research.security import resolve_securities

    limits = _limits(request)
    securities, security_notes = resolve_securities(db, request.query, request.entity_ids)
    scope_ids = list(request.entity_ids)
    if not scope_ids:
        scope_ids = []
        for row in securities:
            try:
                scope_ids.append(uuid.UUID(str(row["entity_id"])))
            except (TypeError, ValueError, KeyError):
                continue
    entities = load_entities(db, scope_ids, limit=limits["entities"])
    events = load_events(
        db,
        entity_ids=scope_ids,
        event_ids=request.event_ids,
        date_from=request.date_from,
        date_to=request.date_to,
        limit=limits["events"],
    )
    event_ids = [uuid.UUID(item["event_id"]) for item in events]
    if request.event_ids:
        event_ids = list(dict.fromkeys(list(request.event_ids) + event_ids))
    graph_facts = load_graph_facts(db, scope_ids, limit=limits["relationships"])
    market_facts = load_market_facts(db, event_ids, securities=securities, limit=limits["events"])
    semantic = search(
        db,
        request.query,
        entity_ids=request.entity_ids or None,
        event_ids=request.event_ids or None,
        document_types=request.document_types or None,
        published_after=request.date_from,
        published_before=request.date_to,
        top_k=limits["chunks"],
        provider=provider,
    )
    documents = [hit_payload(hit) for hit in semantic.get("hits") or []][: limits["documents"]]

    kept_entities, entity_cites = _keep(entities, _cite_entity)
    kept_events, event_cites = _keep(events, _cite_event)
    kept_graph, graph_cites = _keep(graph_facts, _cite_graph)
    kept_market, market_cites = _keep(market_facts, _cite_market)
    kept_docs, doc_cites = _keep(documents, _cite_document)
    _bind_document_evidence(db, kept_docs, doc_cites)
    citations = entity_cites + event_cites + graph_cites + market_cites + doc_cites
    citations.sort(key=lambda item: item["citation_id"])

    package = {
        "request": {
            "query": request.query,
            "entity_ids": [str(item) for item in request.entity_ids],
            "event_ids": [str(item) for item in request.event_ids],
            "date_from": request.date_from.isoformat() if request.date_from else None,
            "date_to": request.date_to.isoformat() if request.date_to else None,
            "document_types": list(request.document_types),
        },
        "entities": kept_entities,
        "events": kept_events,
        "resolved_securities": securities,
        "market_facts": kept_market,
        "graph_facts": kept_graph,
        "documents": kept_docs,
        "citations": citations,
        "retrieval_metadata": {
            "semantic_available": bool(semantic.get("available")),
            "semantic_reason": semantic.get("reason"),
            "citation_coverage": 1.0
            if not (entities or events or graph_facts or market_facts or documents)
            else round(len(citations) / max(1, len(kept_entities) + len(kept_events) + len(kept_graph) + len(kept_market) + len(kept_docs)), 4),
            "citation_url_coverage": citation_url_coverage(citations),
            "limits": limits,
            "security_notes": security_notes,
        },
    }
    RETRIEVAL_METRICS.inc("context_packages_total", 1, operation="context", status="ok", component="retrieval")
    log_event(
        logger,
        20,
        "EVIDENCE_PACKAGE_BUILT",
        extra_data={"component": "retrieval", "operation": "context", "status": "ok"},
    )
    return package


def _keep(items: Sequence[Dict[str, Any]], cite) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    for item in items:
        citation = cite(item)
        if citation is None:
            continue
        kept.append(item)
        citations.append(citation)
    return kept, citations

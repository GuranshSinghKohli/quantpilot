"""Hybrid retrieval: structured filters + vector rank + document diversity."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.domain.models import (
    ChunkEmbedding,
    DocumentEntity,
    Event,
    RetrievalChunk,
    RetrievalDocument,
    Source,
)
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from app.retrieval.metrics import RETRIEVAL_METRICS
from app.retrieval.time import window as utc_window


@dataclass
class RetrievalHit:
    chunk_id: str
    document_id: uuid.UUID
    score: float
    title: Optional[str]
    source_id: uuid.UUID
    source_url: str
    published_at: Optional[datetime]
    excerpt: str
    document_type: str
    entity_ids: List[uuid.UUID]
    event_ids: List[uuid.UUID]
    start_offset: int
    end_offset: int


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)


def _as_vector(value: Any) -> List[float]:
    if value is None:
        return []
    return [float(item) for item in list(value)]


def _filter_documents(
    db: Session,
    stmt: Select,
    *,
    entity_ids: Optional[Sequence[uuid.UUID]],
    event_ids: Optional[Sequence[uuid.UUID]],
    document_types: Optional[Sequence[str]],
    published_after: Optional[datetime],
    published_before: Optional[datetime],
) -> Select:
    """``published_before`` is exclusive. Missing ``published_at`` does not match a window."""
    if document_types:
        stmt = stmt.where(RetrievalDocument.document_type.in_(list(document_types)))
    start, end = utc_window(published_after, published_before)
    if start is not None or end is not None:
        stmt = stmt.where(RetrievalDocument.published_at.is_not(None))
    if start is not None:
        stmt = stmt.where(RetrievalDocument.published_at >= start)
    if end is not None:
        stmt = stmt.where(RetrievalDocument.published_at < end)
    if entity_ids:
        linked = select(DocumentEntity.document_id).where(DocumentEntity.entity_id.in_(list(entity_ids)))
        stmt = stmt.where(RetrievalDocument.id.in_(linked))
    if event_ids:
        sources = select(Event.primary_source_id).where(
            Event.id.in_(list(event_ids)), Event.primary_source_id.is_not(None)
        )
        stmt = stmt.where(RetrievalDocument.source_id.in_(sources))
    return stmt


def search(
    db: Session,
    query: str,
    *,
    entity_ids: Optional[Sequence[uuid.UUID]] = None,
    event_ids: Optional[Sequence[uuid.UUID]] = None,
    document_types: Optional[Sequence[str]] = None,
    published_after: Optional[datetime] = None,
    published_before: Optional[datetime] = None,
    top_k: int = 10,
    provider: Optional[EmbeddingProvider] = None,
    diversity: bool = True,
) -> Dict[str, Any]:
    settings = get_settings()
    limit = min(max(top_k, 1), settings.RETRIEVAL_MAX_TOP_K)
    provider = provider or get_embedding_provider()
    RETRIEVAL_METRICS.inc("retrieval_requests_total", 1, operation="search", component="retrieval")

    doc_stmt = _filter_documents(
        db,
        select(RetrievalDocument),
        entity_ids=entity_ids,
        event_ids=event_ids,
        document_types=document_types,
        published_after=published_after,
        published_before=published_before,
    )
    documents = {row.id: row for row in db.scalars(doc_stmt)}
    if not documents:
        RETRIEVAL_METRICS.inc("retrieval_empty_total", 1, operation="search", result="empty")
        return {"available": True, "hits": [], "reason": None}

    embed_stmt = (
        select(RetrievalChunk, ChunkEmbedding, RetrievalDocument, Source)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == RetrievalChunk.id)
        .join(RetrievalDocument, RetrievalDocument.id == RetrievalChunk.document_id)
        .join(Source, Source.id == RetrievalDocument.source_id)
        .where(RetrievalChunk.document_id.in_(list(documents)))
        .where(ChunkEmbedding.model_name == provider.model_name)
        .where(ChunkEmbedding.model_version == provider.model_version)
        .where(ChunkEmbedding.dimension == provider.dimension)
        .where(RetrievalChunk.document_content_hash == RetrievalDocument.content_hash)
    )
    rows = list(db.execute(embed_stmt))
    if not rows:
        RETRIEVAL_METRICS.inc("retrieval_empty_total", 1, operation="search", result="unavailable")
        return {"available": False, "hits": [], "reason": "semantic_unavailable"}

    query_vec = provider.embed_query(query)
    scored: List[tuple[float, RetrievalChunk, RetrievalDocument, Source]] = []
    for chunk, embedding, document, source in rows:
        if embedding.content_hash != chunk.content_hash:
            continue
        vec = _as_vector(embedding.embedding)
        if len(vec) != provider.dimension:
            continue
        scored.append((cosine(query_vec, vec), chunk, document, source))

    scored.sort(key=lambda item: (-item[0], str(item[2].id), item[1].chunk_index))
    if diversity:
        seen = set()
        unique = []
        for item in scored:
            if item[2].id in seen:
                continue
            seen.add(item[2].id)
            unique.append(item)
        scored = unique

    hits: List[RetrievalHit] = []
    for score, chunk, document, source in scored[:limit]:
        entity_rows = list(
            db.scalars(select(DocumentEntity.entity_id).where(DocumentEntity.document_id == document.id))
        )
        event_rows = list(
            db.scalars(select(Event.id).where(Event.primary_source_id == document.source_id))
        )
        hits.append(
            RetrievalHit(
                chunk_id=chunk.id,
                document_id=document.id,
                score=score,
                title=document.title,
                source_id=source.id,
                source_url=source.url,
                published_at=document.published_at,
                excerpt=chunk.content[: settings.RETRIEVAL_MAX_EXCERPT],
                document_type=document.document_type,
                entity_ids=list(entity_rows),
                event_ids=list(event_rows),
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
        )
    RETRIEVAL_METRICS.inc("retrieval_results_total", len(hits), operation="search", result="ok")
    return {"available": True, "hits": hits, "reason": None}


def hit_payload(hit: RetrievalHit) -> Dict[str, Any]:
    return {
        "chunk_id": hit.chunk_id,
        "document_id": str(hit.document_id),
        "score": hit.score,
        "title": hit.title,
        "source_id": str(hit.source_id),
        "source_url": hit.source_url,
        "published_at": hit.published_at.isoformat() if hit.published_at else None,
        "excerpt": hit.excerpt,
        "document_type": hit.document_type,
        "entity_ids": [str(item) for item in hit.entity_ids],
        "event_ids": [str(item) for item in hit.event_ids],
        "locator": {"char_start": hit.start_offset, "char_end": hit.end_offset},
    }

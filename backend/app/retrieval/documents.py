"""Index canonical sources/news into retrieval documents and chunks."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentType, EntityType, ResolutionStatus, SourceType
from app.domain.identity import content_hash, document_identity, document_uuid
from app.domain.ingestion_models import NewsArticle
from app.domain.models import DocumentEntity, RetrievalChunk, RetrievalDocument, Source
from app.graph.resolve import EntityRef, resolve_entity
from app.observability.logger import get_logger, log_event
from app.retrieval.chunking import chunk_document
from app.retrieval.metrics import RETRIEVAL_METRICS
from app.retrieval.normalize import is_usable_text, normalize_document_text

logger = get_logger("retrieval")

_SOURCE_TO_DOC = {
    SourceType.NEWS.value: DocumentType.NEWS_ARTICLE.value,
    SourceType.FILING.value: DocumentType.SEC_FILING.value,
    SourceType.PRESS_RELEASE.value: DocumentType.COMPANY_DOCUMENT.value,
    SourceType.RESEARCH.value: DocumentType.RESEARCH_DOCUMENT.value,
}


def document_type_for_source(source_type: str) -> str:
    return _SOURCE_TO_DOC.get(source_type, DocumentType.OTHER.value)


def _usable_body(source: Source, article: Optional[NewsArticle]) -> str:
    parts = [source.title or ""]
    if article is not None and article.summary:
        parts.append(article.summary)
    elif isinstance(source.metadata_, dict):
        extra = source.metadata_.get("summary") or source.metadata_.get("text")
        if extra:
            parts.append(str(extra))
    return normalize_document_text("\n\n".join(part for part in parts if part))


def _persist_chunks(db: Session, document: RetrievalDocument) -> int:
    specs = chunk_document(document.id, document.content_hash, document.normalized_text)
    created = 0
    for spec in specs:
        existing = db.get(RetrievalChunk, spec.chunk_id)
        if existing is not None:
            continue
        db.add(
            RetrievalChunk(
                id=spec.chunk_id,
                document_id=document.id,
                chunk_index=spec.chunk_index,
                start_offset=spec.start_offset,
                end_offset=spec.end_offset,
                content=spec.content,
                content_hash=spec.content_hash,
                document_content_hash=document.content_hash,
            )
        )
        created += 1
    return created


def _link_tickers(db: Session, document: RetrievalDocument, tickers: list[str], source_id: uuid.UUID) -> int:
    linked = 0
    for ticker in tickers:
        result = resolve_entity(db, EntityRef(ticker=ticker, entity_type=EntityType.COMPANY))
        if result.status != ResolutionStatus.RESOLVED or result.entity_id is None:
            continue
        exists = db.get(DocumentEntity, (document.id, result.entity_id))
        if exists is not None:
            continue
        db.add(DocumentEntity(document_id=document.id, entity_id=result.entity_id, source_id=source_id))
        linked += 1
    return linked


def upsert_document_from_source(
    db: Session, source: Source, article: Optional[NewsArticle] = None
) -> Dict[str, Any]:
    body = _usable_body(source, article)
    if not is_usable_text(body):
        return {"status": "skipped", "reason": "empty"}
    identity = document_identity(
        article.source_key if article else None,
        article.external_id if article else None,
        source.url_hash,
    )
    digest = content_hash(body)
    document_id = document_uuid(identity)
    existing = db.get(RetrievalDocument, document_id)
    if existing is None:
        existing = db.scalars(select(RetrievalDocument).where(RetrievalDocument.source_id == source.id)).first()
    if existing is not None and existing.content_hash == digest:
        chunks = _persist_chunks(db, existing)
        db.flush()
        return {"status": "skipped", "reason": "unchanged", "document_id": str(existing.id), "chunks": chunks}

    if existing is None:
        document = RetrievalDocument(
            id=document_id,
            source_id=source.id,
            identity_key=identity,
            document_type=document_type_for_source(source.source_type),
            external_id=article.external_id if article else None,
            title=source.title,
            canonical_url=source.url,
            publisher=source.publisher,
            published_at=source.published_at,
            normalized_text=body,
            content_hash=digest,
            content_length=len(body),
            content_version=1,
        )
        db.add(document)
        db.flush()
        chunks = _persist_chunks(db, document)
        tickers = list(article.tickers or []) if article is not None else list((source.metadata_ or {}).get("tickers") or [])
        _link_tickers(db, document, [str(t) for t in tickers], source.id)
        db.flush()
        return {"status": "inserted", "document_id": str(document.id), "chunks": chunks}

    existing.previous_content_hash = existing.content_hash
    existing.content_version += 1
    existing.normalized_text = body
    existing.content_hash = digest
    existing.content_length = len(body)
    existing.title = source.title
    existing.publisher = source.publisher
    existing.published_at = source.published_at
    existing.canonical_url = source.url
    db.flush()
    chunks = _persist_chunks(db, existing)
    db.flush()
    return {"status": "updated", "document_id": str(existing.id), "chunks": chunks, "version": existing.content_version}


def index_documents(db: Session) -> Dict[str, int]:
    started = time.perf_counter()
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0, "chunks": 0}
    articles = {row.source_id: row for row in db.scalars(select(NewsArticle))}
    for source in db.scalars(select(Source)):
        try:
            report = upsert_document_from_source(db, source, articles.get(source.id))
            status = report["status"]
            if status in counts:
                counts[status] += 1
            counts["chunks"] += int(report.get("chunks") or 0)
        except Exception:
            counts["failed"] += 1
            RETRIEVAL_METRICS.inc(
                "embedding_documents_total", 1, operation="index", status="failed", component="retrieval"
            )
    duration = time.perf_counter() - started
    RETRIEVAL_METRICS.observe("retrieval_duration_seconds", duration, operation="index", component="retrieval")
    log_event(
        logger,
        20,
        "RETRIEVAL_DOCUMENTS_INDEXED",
        extra_data={"component": "retrieval", "operation": "index", "status": "ok", "duration_ms": int(duration * 1000)},
    )
    return counts

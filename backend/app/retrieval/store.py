"""Idempotent embedding persistence. Canonical source rows are never written here."""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.domain.models import ChunkEmbedding, RetrievalChunk, RetrievalDocument
from app.observability.logger import get_logger, log_event
from app.retrieval.embeddings import EmbeddingError, EmbeddingProvider, get_embedding_provider
from app.retrieval.metrics import RETRIEVAL_METRICS

logger = get_logger("retrieval")


def _pending_chunks(
    db: Session,
    *,
    document_id: Optional[uuid.UUID],
    model_name: str,
    model_version: str,
) -> List[RetrievalChunk]:
    stmt = select(RetrievalChunk)
    if document_id is not None:
        stmt = stmt.where(RetrievalChunk.document_id == document_id)
    chunks = list(db.scalars(stmt))
    pending = []
    for chunk in chunks:
        row = db.scalars(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk.id,
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.model_version == model_version,
            )
        ).first()
        if row is None or row.content_hash != chunk.content_hash:
            pending.append(chunk)
    return pending


def persist_embeddings(
    db: Session,
    chunks: Sequence[RetrievalChunk],
    vectors: Sequence[Sequence[float]],
    provider: EmbeddingProvider,
) -> int:
    written = 0
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != provider.dimension:
            raise EmbeddingError("embedding dimension mismatch", retryable=False)
        row = db.scalars(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk.id,
                ChunkEmbedding.model_name == provider.model_name,
                ChunkEmbedding.model_version == provider.model_version,
            )
        ).first()
        if row is None:
            db.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    model_name=provider.model_name,
                    model_version=provider.model_version,
                    dimension=provider.dimension,
                    embedding=list(vector),
                    content_hash=chunk.content_hash,
                )
            )
            written += 1
            continue
        if row.content_hash == chunk.content_hash and list(row.embedding) == list(vector):
            continue
        row.embedding = list(vector)
        row.content_hash = chunk.content_hash
        row.dimension = provider.dimension
        written += 1
    return written


def embed_documents(
    db: Session,
    *,
    document_id: Optional[uuid.UUID] = None,
    provider: Optional[EmbeddingProvider] = None,
) -> Dict[str, int]:
    started = time.perf_counter()
    settings = get_settings()
    provider = provider or get_embedding_provider()
    pending = _pending_chunks(
        db,
        document_id=document_id,
        model_name=provider.model_name,
        model_version=provider.model_version,
    )
    counts = {"embedded": 0, "skipped": 0, "failed": 0, "retried": 0}
    if document_id is not None and db.get(RetrievalDocument, document_id) is None and not pending:
        return counts
    batch = settings.EMBEDDING_BATCH_SIZE
    for start in range(0, len(pending), batch):
        group = pending[start : start + batch]
        try:
            vectors = provider.embed_texts([chunk.content for chunk in group])
            counts["embedded"] += persist_embeddings(db, group, vectors, provider)
            RETRIEVAL_METRICS.inc(
                "embedding_chunks_total",
                len(group),
                operation="embed",
                status="ok",
                provider=settings.EMBEDDING_PROVIDER,
                model=provider.model_name,
            )
        except EmbeddingError:
            counts["failed"] += len(group)
            RETRIEVAL_METRICS.inc(
                "embedding_failures_total",
                1,
                operation="embed",
                status="failed",
                provider=settings.EMBEDDING_PROVIDER,
            )
            raise
    selected_stmt = select(RetrievalChunk)
    if document_id is not None:
        selected_stmt = selected_stmt.where(RetrievalChunk.document_id == document_id)
    selected = list(db.scalars(selected_stmt))
    counts["skipped"] = max(0, len(selected) - len(pending))
    duration = time.perf_counter() - started
    RETRIEVAL_METRICS.inc("embedding_jobs_total", 1, operation="embed", status="ok", component="retrieval")
    log_event(
        logger,
        20,
        "RETRIEVAL_EMBEDDED",
        extra_data={
            "component": "retrieval",
            "operation": "embed",
            "status": "ok",
            "provider": settings.EMBEDDING_PROVIDER,
            "model": provider.model_name,
            "duration_ms": int(duration * 1000),
        },
    )
    return counts

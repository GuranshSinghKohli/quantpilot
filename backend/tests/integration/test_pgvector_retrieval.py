"""Nearest-neighbor search on disposable PostgreSQL + pgvector."""

from __future__ import annotations

from app.domain.enums import SourceType
from app.domain.models import ChunkEmbedding
from app.domain.schemas import SourceIn
from app.repositories import sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.search import search
from app.retrieval.store import embed_documents
from tests.unit.retrieval.helpers import NOW


def test_pgvector_nearest_neighbor_and_filters(db):
    sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/pg/nvda",
            retrieved_at=NOW,
            content_text="NVIDIA and Meta announced a multi-year inference partnership.",
            title="NVIDIA Meta partnership",
            published_at=NOW,
            metadata={"summary": "NVIDIA and Meta announced a multi-year inference partnership."},
        ),
    )
    sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/pg/bakery",
            retrieved_at=NOW,
            content_text="A bakery opened a second shop downtown.",
            title="Bakery opens",
            published_at=NOW,
            metadata={"summary": "A bakery opened a second shop downtown."},
        ),
    )
    index_documents(db)
    provider = FakeEmbeddingProvider()
    embed_documents(db, provider=provider)
    assert db.query(ChunkEmbedding).count() >= 2
    result = search(db, "NVIDIA Meta inference partnership", top_k=2, provider=provider)
    assert result["available"] is True
    assert result["hits"]
    assert result["hits"][0].title == "NVIDIA Meta partnership"
    empty = search(db, "NVIDIA", document_types=["sec_filing"], provider=provider)
    assert empty["hits"] == []
    again = search(db, "NVIDIA Meta inference partnership", top_k=2, provider=provider)
    assert [hit.chunk_id for hit in again["hits"]] == [hit.chunk_id for hit in result["hits"]]

"""The same half-open publication window on disposable PostgreSQL."""

from datetime import datetime, timedelta, timezone

from app.domain.enums import SourceType
from app.domain.schemas import SourceIn
from app.repositories import sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.search import search
from app.retrieval.store import embed_documents

START = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def test_postgres_end_is_exclusive_and_ingestion_is_not_the_filter(db):
    sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/pg-date-start",
            retrieved_at=START + timedelta(days=30),
            content_text="alpha report at the start of the window",
            title="alpha report at the start of the window",
            published_at=START,
            metadata={"summary": "alpha report at the start of the window"},
        ),
    )
    sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url="https://example.com/pg-date-end",
            retrieved_at=START,
            content_text="alpha report at the exclusive end",
            title="alpha report at the exclusive end",
            published_at=START + timedelta(days=1),
            metadata={"summary": "alpha report at the exclusive end"},
        ),
    )
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider())
    result = search(
        db,
        "alpha report",
        published_after=START,
        published_before=START + timedelta(days=1),
        top_k=10,
        provider=FakeEmbeddingProvider(),
    )
    assert {hit.title for hit in result["hits"]} == {"alpha report at the start of the window"}

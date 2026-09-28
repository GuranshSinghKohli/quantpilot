"""Fake embeddings, idempotent persist, hybrid search, no network."""

from __future__ import annotations

import pytest

from app.pipeline.metrics import FORBIDDEN_LABELS, MetricLabelError
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import EmbeddingError, FakeEmbeddingProvider
from app.retrieval.metrics import RETRIEVAL_METRICS
from app.retrieval.search import cosine, search
from app.retrieval.store import embed_documents
from tests.unit.retrieval.helpers import close_db, company, migrated_db, news_source


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_fake_provider_is_deterministic_and_normalized():
    provider = FakeEmbeddingProvider(dimension=64)
    a = provider.embed_query("NVIDIA Meta partnership")
    b = provider.embed_query("NVIDIA Meta partnership")
    c = provider.embed_query("bakery weather lisbon")
    assert a == b
    assert len(a) == 64
    assert pytest.approx(sum(x * x for x in a), abs=1e-6) == 1.0
    assert cosine(a, b) == pytest.approx(1.0)
    assert cosine(a, c) < cosine(a, provider.embed_query("NVIDIA partnership inference"))


def test_embed_is_idempotent_and_skips_unchanged(db):
    news_source(db, "https://example.com/e1", "NVIDIA partners with Meta", "NVIDIA and Meta announced inference GPUs.")
    index_documents(db)
    provider = FakeEmbeddingProvider()
    first = embed_documents(db, provider=provider)
    second = embed_documents(db, provider=provider)
    assert first["embedded"] >= 1
    assert second["embedded"] == 0
    assert second["skipped"] >= 1


def test_model_change_does_not_overwrite(db):
    news_source(db, "https://example.com/e2", "AMD rack", "AMD published a rack-scale design.")
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider(model_name="fake-a", model_version="v1"))
    embed_documents(db, provider=FakeEmbeddingProvider(model_name="fake-b", model_version="v1"))
    from app.domain.models import ChunkEmbedding

    models = {row.model_name for row in db.query(ChunkEmbedding)}
    assert models == {"fake-a", "fake-b"}


def test_provider_failure_does_not_write_embeddings(db):
    news_source(db, "https://example.com/e3", "Fail doc", "This document should not be marked embedded.")
    index_documents(db)

    class Boom(FakeEmbeddingProvider):
        def embed_texts(self, texts):
            raise EmbeddingError("provider down", retryable=True)

    with pytest.raises(EmbeddingError):
        embed_documents(db, provider=Boom())
    from app.domain.models import ChunkEmbedding, Source

    assert db.query(ChunkEmbedding).count() == 0
    assert db.query(Source).count() == 1


def test_hybrid_entity_filter_and_diversity(db):
    company(db, "NVIDIA Corporation", "NVDA", "1045810")
    company(db, "Meta Platforms Inc", "META", "1326801")
    news_source(db, "https://example.com/h1", "NVIDIA partners with Meta", "NVIDIA and Meta announced a partnership.", ["NVDA", "META"])
    news_source(db, "https://example.com/h2", "Local bakery opens", "A bakery opened downtown with no public companies.")
    news_source(
        db,
        "https://example.com/h3",
        "NVIDIA partners extra paragraph",
        "NVIDIA and Meta announced a partnership. Extra sentence for a second chunk about the same source.",
        ["NVDA"],
    )
    index_documents(db)
    provider = FakeEmbeddingProvider()
    embed_documents(db, provider=provider)
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    result = search(db, "NVIDIA Meta partnership", entity_ids=[nvda.entity_id], top_k=10, provider=provider)
    assert result["available"]
    titles = [hit.title for hit in result["hits"]]
    assert "Local bakery opens" not in titles
    assert any("NVIDIA" in (title or "") for title in titles)
    docs = [hit.document_id for hit in result["hits"]]
    assert len(docs) == len(set(docs))


def test_metrics_reject_query_labels():
    with pytest.raises(MetricLabelError):
        RETRIEVAL_METRICS.inc("retrieval_requests_total", 1, query="secret")
    assert "query" in FORBIDDEN_LABELS
    assert "document_id" in FORBIDDEN_LABELS
    assert "chunk_id" in FORBIDDEN_LABELS

"""Fixture timings and the development evaluation set."""

from __future__ import annotations

import time

import pytest

from app.retrieval.chunking import chunk_document
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.evaluate import evaluate_retrieval
from app.retrieval.normalize import normalize_document_text
from app.retrieval.package import ResearchRequest, assemble_package
from app.retrieval.search import search
from app.retrieval.store import embed_documents
from tests.unit.retrieval.helpers import close_db, migrated_db, news_source
from app.domain.identity import content_hash, document_uuid


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_fixture_performance_and_eval(db, capsys):
    text = normalize_document_text("NVIDIA announced a partnership.\n\n" + ("More context. " * 40))
    t0 = time.perf_counter()
    normalize_document_text(text)
    norm_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    chunk_document(document_uuid("perf"), content_hash(text), text)
    chunk_ms = (time.perf_counter() - t1) * 1000

    news_source(db, "https://example.com/perf", "NVIDIA partners", "NVIDIA and Meta announced a partnership.")
    t2 = time.perf_counter()
    index_documents(db)
    index_ms = (time.perf_counter() - t2) * 1000
    provider = FakeEmbeddingProvider()
    t3 = time.perf_counter()
    embed_documents(db, provider=provider)
    embed_ms = (time.perf_counter() - t3) * 1000
    t4 = time.perf_counter()
    search(db, "NVIDIA partnership", provider=provider)
    search_ms = (time.perf_counter() - t4) * 1000
    t5 = time.perf_counter()
    assemble_package(db, ResearchRequest(query="NVIDIA partnership"), provider=provider)
    pack_ms = (time.perf_counter() - t5) * 1000
    t6 = time.perf_counter()
    report = evaluate_retrieval(db)
    eval_ms = (time.perf_counter() - t6) * 1000
    print(
        f"PERF normalize={norm_ms:.2f}ms chunk={chunk_ms:.2f}ms index={index_ms:.2f}ms "
        f"embed={embed_ms:.2f}ms search={search_ms:.2f}ms package={pack_ms:.2f}ms eval={eval_ms:.2f}ms"
    )
    print("EVAL", report)
    assert report["query_count"] == 8
    assert report["empty_rate"] <= 1.0
    assert norm_ms < 1000
    assert eval_ms < 30000

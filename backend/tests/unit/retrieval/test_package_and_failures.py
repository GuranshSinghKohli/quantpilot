"""EvidencePackage limits, citations, and failure isolation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from app.domain.enums import EventEntityRole, EventStatus, EventType, RelationshipType
from app.domain.schemas import EventEntityIn, EventIn, EvidenceIn
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import events, sources
from app.retrieval.documents import index_documents
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import RequestOversized, ResearchRequest, assemble_package
from app.retrieval.search import search
from app.retrieval.store import embed_documents
from tests.unit.retrieval.helpers import NOW, close_db, company, migrated_db, news_source


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_package_is_bounded_cited_and_serializable(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    meta = company(db, "Meta Platforms Inc", "META", "1326801")
    src = news_source(db, "https://example.com/p1", "NVIDIA partners with Meta", "NVIDIA and Meta announced a partnership.", ["NVDA", "META"])
    ev, _ = sources.add_evidence(db, src.id, EvidenceIn(excerpt="NVIDIA and Meta announced a partnership.", extraction_method="test", extracted_at=NOW))
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=EventType.PARTNERSHIP,
            title="NVIDIA Meta partnership",
            detected_at=NOW,
            occurred_at=NOW,
            published_at=NOW,
            primary_source_id=src.id,
            evidence_id=ev.id,
            entities=[EventEntityIn(entity_id=nvda.entity_id, role=EventEntityRole.SUBJECT, source_id=src.id, evidence_id=ev.id)],
        ),
    )
    event.status = EventStatus.CONFIRMED.value
    persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=nvda.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=meta.entity_id),
            source_id=src.id,
            evidence_id=ev.id,
            confidence=1.0,
            observed_at=NOW,
        ),
    )
    index_documents(db)
    provider = FakeEmbeddingProvider()
    embed_documents(db, provider=provider)
    package = assemble_package(
        db,
        ResearchRequest(query="NVIDIA Meta partnership", entity_ids=[nvda.entity_id], event_ids=[event.id]),
        provider=provider,
    )
    blob = json.dumps(package, default=str)
    assert "NVIDIA" in blob
    assert package["retrieval_metadata"]["citation_coverage"] == 1.0
    for item in package["citations"]:
        assert item["citation_id"]
    assert all(doc.get("source_url") for doc in package["documents"])
    with pytest.raises(RequestOversized):
        assemble_package(db, ResearchRequest(query="x", max_documents=9999), provider=provider)


def test_graph_edge_without_evidence_is_excluded(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    package = assemble_package(db, ResearchRequest(query="anything", entity_ids=[nvda.entity_id]))
    assert package["graph_facts"] == []


def test_semantic_unavailable_still_returns_structured(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    result = search(db, "NVIDIA", entity_ids=[nvda.entity_id])
    assert result["hits"] == []
    package = assemble_package(db, ResearchRequest(query="NVIDIA", entity_ids=[nvda.entity_id]))
    assert package["entities"]
    assert package["entities"][0]["ticker"] == "NVDA"
    assert package["documents"] == []


def test_api_search_and_context_are_bounded(db):
    from app.routers.retrieval import read_search

    news_source(db, "https://example.com/api1", "NVIDIA partners", "NVIDIA announced a partnership.")
    index_documents(db)
    embed_documents(db, provider=FakeEmbeddingProvider())
    body = read_search(q="NVIDIA partnership", top_k=5, db=db)
    assert "results" in body
    assert "embedding" not in body
    with pytest.raises(RequestOversized):
        assemble_package(db, ResearchRequest(query="NVIDIA partnership", max_documents=9999))

"""Approved tools: permissions, provenance, no SQL."""

from __future__ import annotations

import pytest
from app.research.tools import invoke_tool
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from tests.unit.research.helpers import close_db, db_session, seed_partnership


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_tool_permission_denied(db):
    result = invoke_tool("get_market_impact", "news", db, event_ids=[])
    assert result.errors == ["tool_not_permitted:get_market_impact"]


def test_get_entity_and_relationships_keep_provenance(db):
    world = seed_partnership(db)
    entities = invoke_tool("get_entity", "company", db, entity_ids=[world["amd"].entity_id])
    assert entities.data
    assert entities.data[0]["source"]["record"]
    rels = invoke_tool("get_entity_relationships", "company", db, entity_ids=[world["amd"].entity_id])
    assert rels.data
    assert rels.evidence_ids
    assert rels.source_ids


def test_retrieve_documents_and_verify_citation(db):
    world = seed_partnership(db)
    request = ResearchRequest(query="AMD Meta partnership", entity_ids=[world["amd"].entity_id])
    docs = invoke_tool(
        "retrieve_documents",
        "news",
        db,
        request=request,
        query=request.query,
        entity_ids=request.entity_ids,
        provider=FakeEmbeddingProvider(),
    )
    assert docs.data["available"] is True
    from app.retrieval.package import assemble_package

    package = assemble_package(db, request, provider=FakeEmbeddingProvider())
    cites = [item["citation_id"] for item in package["citations"]]
    checked = invoke_tool("verify_evidence", "evidence", db, package=package, citation_ids=cites[:1])
    assert checked.errors == []
    missing = invoke_tool("verify_evidence", "news", db, package=package, citation_ids=["nope"])
    assert missing.errors == ["unknown_citation:nope"]


def test_retrieve_context_reserved_for_graph(db):
    request = ResearchRequest(query="AMD")
    denied = invoke_tool("retrieve_context", "company", db, request=request)
    assert denied.errors == ["tool_not_permitted:retrieve_context"]
    ok = invoke_tool("retrieve_context", "graph", db, request=request, provider=FakeEmbeddingProvider())
    assert ok.data["citations"] is not None

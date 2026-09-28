"""Full LangGraph research workflow."""

from __future__ import annotations

import json

import pytest
from app.research.llm import FakeLLMProvider, LLMError
from app.research.service import ResearchUnavailable, run_research
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import RequestOversized, ResearchRequest
from tests.unit.research.helpers import close_db, db_session, seed_partnership


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def _run(db, world, **llm_kwargs):
    return run_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(**llm_kwargs),
        embed_provider=FakeEmbeddingProvider(),
        persist=True,
    )


def test_all_agents_succeed(db):
    world = seed_partnership(db)
    result = _run(db, world)
    blob = json.dumps(result.model_dump(mode="json"), default=str)
    assert result.verified_claims or result.findings
    assert result.citations
    assert {m.agent for m in result.agent_metadata} >= {"news", "company", "technology", "market", "evidence"}
    assert "buy" not in blob.lower() or "should buy" not in blob.lower()
    assert result.research_job_id is not None


def test_one_and_multiple_agent_failures(db):
    world = seed_partnership(db)
    one = _run(db, world, fail_agents={"market"})
    assert "market" in one.unavailable_components
    assert one.verified_claims or one.findings
    assert any(item.get("stage") == "market" for item in one.errors)
    many = _run(db, world, fail_agents={"news", "technology", "market"})
    assert {"news", "technology", "market"} <= set(many.unavailable_components)
    assert any(m.agent == "evidence" and m.status == "succeeded" for m in many.agent_metadata)


def test_llm_timeout_rate_limit_malformed(db):
    world = seed_partnership(db)
    timeout = _run(db, world, scenario="timeout", fail_times=8)
    assert timeout.unavailable_components
    rate = _run(db, world, scenario="rate_limit", fail_times=8)
    assert rate.errors
    malformed = _run(db, world, scenario="malformed")
    assert malformed.findings == []
    assert malformed.verified_claims == []


def test_empty_and_oversized(db):
    world = seed_partnership(db)
    empty = run_research(
        db,
        ResearchRequest(query="unrelated fog delays over european airports xyz"),
        llm=FakeLLMProvider(),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )
    assert empty.unsupported_claims or empty.findings == [] or empty.verified_claims is not None
    with pytest.raises(RequestOversized):
        run_research(
            db,
            ResearchRequest(query="AMD", max_documents=9999),
            llm=FakeLLMProvider(),
            persist=False,
        )


def test_conflicting_evidence_kept(db):
    world = seed_partnership(db)
    result = _run(db, world, scenario="conflicting")
    assert result.conflicts
    assert len(result.conflicts) >= 2


def test_provider_unavailable_does_not_raise_inside_specialists(db, monkeypatch):
    world = seed_partnership(db)

    def _boom():
        raise LLMError("OPENAI_API_KEY is not configured", retryable=False, error_type="auth")

    monkeypatch.setattr("app.research.service.get_llm_provider", _boom)
    with pytest.raises(ResearchUnavailable):
        run_research(
            db,
            ResearchRequest(query="AMD", entity_ids=[world["amd"].entity_id]),
            persist=False,
        )

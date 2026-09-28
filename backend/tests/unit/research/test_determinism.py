"""Same package + FakeLLM + prompt versions → same ResearchResult."""

from __future__ import annotations

import json

import pytest
from app.research.llm import FakeLLMProvider
from app.research.service import run_research
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


def _payload(result):
    data = result.model_dump(mode="json")
    data.pop("created_at", None)
    data.pop("research_job_id", None)
    for meta in data.get("agent_metadata") or []:
        meta.pop("started_at", None)
        meta.pop("completed_at", None)
        meta.pop("duration_ms", None)
        meta.pop("input_tokens", None)
        meta.pop("output_tokens", None)
        meta.pop("total_tokens", None)
    return data


def test_repeated_research_is_deterministic(db):
    world = seed_partnership(db)
    request = ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id])
    a = run_research(db, request, llm=FakeLLMProvider(), embed_provider=FakeEmbeddingProvider(), persist=False)
    b = run_research(db, request, llm=FakeLLMProvider(), embed_provider=FakeEmbeddingProvider(), persist=False)
    assert json.dumps(_payload(a), sort_keys=True) == json.dumps(_payload(b), sort_keys=True)

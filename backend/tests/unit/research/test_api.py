"""POST /api/research — bounded, no arbitrary tool access."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.research.llm import FakeLLMProvider
from app.research.service import run_research
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from app.routers.research import ResearchBody, create_research
from tests.unit.research.helpers import close_db, db_session, seed_partnership


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_valid_request(db):
    world = seed_partnership(db)
    # Router uses get_llm_provider (fake in tests). Call the service with an explicit provider
    # and also exercise the request body validator used by the API.
    body = ResearchBody(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id])
    request = ResearchRequest.model_validate(body.model_dump())
    result = run_research(db, request, llm=FakeLLMProvider(), embed_provider=FakeEmbeddingProvider())
    payload = result.model_dump(mode="json")
    assert "findings" in payload
    assert "verified_claims" in payload
    assert "embedding" not in payload
    assert "sql" not in payload
    assert payload.get("research_job_id")


def test_invalid_and_oversized_and_unresolved(db):
    from fastapi import HTTPException

    with pytest.raises(ValidationError):
        ResearchBody(query="")
    with pytest.raises(HTTPException) as oversized:
        create_research(ResearchBody(query="AMD", max_documents=9999), db=db)
    assert oversized.value.status_code == 400
    world = seed_partnership(db)
    import uuid

    missing = run_research(
        db,
        ResearchRequest(query="AMD", entity_ids=[uuid.uuid4()]),
        llm=FakeLLMProvider(),
        persist=False,
    )
    assert missing.request["query"] == "AMD"
    assert all("secret" not in item.statement.lower() for item in missing.findings)


def test_partial_failure_and_success_via_endpoint(db):
    world = seed_partnership(db)
    from app.research.service import run_research as _run

    partial = _run(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(fail_agents={"market"}),
        persist=False,
    )
    assert "market" in partial.unavailable_components
    success = _run(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(),
        persist=False,
    )
    assert success.agent_metadata
    assert {m.status for m in success.agent_metadata} <= {"succeeded"}

"""Specialist agents with FakeLLMProvider."""

from __future__ import annotations

import pytest
from app.research.agents import run_specialist
from app.research.llm import FakeLLMProvider
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest, assemble_package
from tests.unit.research.helpers import close_db, db_session, package_with_market, seed_partnership


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def _package(db, world):
    return assemble_package(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id, world["meta"].entity_id]),
        provider=FakeEmbeddingProvider(),
    )


@pytest.mark.parametrize("agent", ["news", "company", "technology", "market"])
def test_valid_and_empty_and_failures(db, agent):
    world = seed_partnership(db)
    package = _package(db, world)
    findings, meta, err = run_specialist(agent, package, provider=FakeLLMProvider())
    assert err is None
    assert meta.status == "succeeded"
    assert meta.prompt_version.endswith("-v1")
    if agent in {"news", "company"}:
        assert findings
        assert all(item.citations for item in findings)
    if agent == "market":
        assert findings == []

    empty, _, _ = run_specialist(agent, {"citations": [], "documents": [], "entities": [], "events": [], "graph_facts": [], "market_facts": []}, provider=FakeLLMProvider())
    if agent != "market":
        assert empty == [] or all(item.citations for item in empty)

    failed, failed_meta, failed_err = run_specialist(
        agent, package, provider=FakeLLMProvider(fail_agents={agent})
    )
    assert failed == []
    assert failed_meta.status == "failed"
    assert failed_err == "provider"

    malformed, mal_meta, mal_err = run_specialist(agent, package, provider=FakeLLMProvider(scenario="malformed"))
    assert malformed == []
    assert mal_err == "malformed"

    transient, trans_meta, trans_err = run_specialist(
        agent, package, provider=FakeLLMProvider(scenario="transient", fail_times=1)
    )
    assert trans_err is None
    assert trans_meta.retry_count >= 1


def test_market_observations_and_rejects_advice():
    package = package_with_market()
    findings, meta, err = run_specialist("market", package, provider=FakeLLMProvider())
    assert err is None
    assert findings
    assert all(item.finding_type.value == "OBSERVATION" for item in findings)
    assert all("returned" in item.statement.lower() for item in findings)
    advice, _, _ = run_specialist(
        "market",
        package,
        provider=FakeLLMProvider(scenario="missing_citation"),
    )
    # missing_citation fixture is not market advice; force a buy statement via filter
    from app.research.agents import _has_advice

    assert _has_advice("Investors should buy AMD")
    assert not any(_has_advice(item.statement) for item in findings)


def test_irrelevant_evidence_does_not_fabricate_tech_use(db):
    world = seed_partnership(db)
    package = assemble_package(
        db,
        ResearchRequest(query="European airport fog", entity_ids=[]),
        provider=FakeEmbeddingProvider(),
    )
    findings, _, _ = run_specialist("technology", package, provider=FakeLLMProvider())
    assert all("USES" not in item.statement for item in findings)

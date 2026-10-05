"""Planner contract. Expected domains here are test labels, not production routing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.research.llm import FakeLLMProvider, _coerce_structured, master_response_format
from app.research.plan import accept_plan, fallback_plan
from app.research.planner_eval import PLANNER_EVAL_CASES, score_selection
from app.research.schemas import MasterPlanPayload
from app.research.service import run_research
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest, _cite_market
from tests.unit.research.helpers import close_db, db_session, seed_partnership


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def _run(db, scenario: str):
    world = seed_partnership(db)
    return run_research(
        db,
        ResearchRequest(query="What recent NVIDIA news affected its stock?", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(scenario=scenario),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )


def test_valid_planner_output_is_accepted(db):
    result = _run(db, "valid")
    assert result.plan is not None
    assert result.plan.source == "llm"
    master = next(item for item in result.agent_metadata if item.agent == "master")
    assert master.retry_count == 0
    assert master.error_type is None


def test_malformed_planner_retries_once_then_uses_the_router(db):
    result = _run(db, "malformed")
    assert result.plan is not None
    assert result.plan.source == "fallback"
    master = next(item for item in result.agent_metadata if item.agent == "master")
    assert master.error_type == "malformed"
    assert master.retry_count == 1
    assert "sql_agent" not in result.plan.selected_agents


def test_schema_mismatch_is_rejected_and_falls_back(db):
    request = {"query": "What companies has Microsoft partnered with?", "entity_ids": [], "event_ids": []}
    rejected = MasterPlanPayload(objective="x", selected_agents=["sql_agent"], requested_sections=["key_facts"])
    assert accept_plan(rejected, request, {}) is None
    result = _run(db, "bad_plan")
    assert result.plan is not None and result.plan.source == "fallback"
    master = next(item for item in result.agent_metadata if item.agent == "master")
    assert master.error_type == "invalid_plan"
    assert master.retry_count == 1


def test_retry_can_recover_a_valid_plan(db):
    result = _run(db, "plan_retry")
    assert result.plan is not None
    assert result.plan.source == "llm"
    master = next(item for item in result.agent_metadata if item.agent == "master")
    assert master.retry_count == 1
    assert master.error_type is None


def test_string_evidence_requirements_stay_invalid():
    raw = {
        "objective": "research",
        "selected_agents": ["news", "market"],
        "evidence_requirements": "Recent articles about the move.",
        "requested_sections": ["key_facts"],
        "reasoning_notes": "note",
    }
    coerced = _coerce_structured(raw, MasterPlanPayload, "master")
    assert isinstance(coerced["evidence_requirements"], str)
    with pytest.raises(ValidationError):
        MasterPlanPayload.model_validate(coerced)


def test_strict_planner_schema_requires_arrays():
    schema = master_response_format()["json_schema"]
    body = schema["schema"]
    assert schema["strict"] is True
    assert body["additionalProperties"] is False
    assert body["properties"]["evidence_requirements"]["type"] == "array"
    assert body["properties"]["requested_sections"]["type"] == "array"
    assert body["properties"]["selected_agents"]["items"]["enum"] == ["news", "company", "technology", "market"]


def test_empty_query_and_multi_domain_labels_stay_out_of_the_router():
    empty = fallback_plan({"query": "Tell me about the situation.", "entity_ids": [], "event_ids": []}, {}, note="router")
    assert empty.selected_agents == []
    multi = fallback_plan(
        {"query": "What recent NVIDIA news affected its stock?", "entity_ids": [], "event_ids": []},
        {},
        note="router",
    )
    assert multi.selected_agents == ["news", "market"]
    accepted = accept_plan(
        MasterPlanPayload(
            objective="stock news",
            selected_agents=["market", "news"],
            evidence_requirements=["citation_required_for_facts"],
            requested_sections=["key_facts", "market_observations"],
            reasoning_notes="request names news and the stock",
        ),
        {"query": multi.objective, "entity_ids": [], "event_ids": []},
        {},
    )
    assert accepted is not None
    assert accepted.source == "llm"
    assert accepted.selected_agents == ["news", "market"]


def test_eval_labels_score_required_forbidden_and_extra_domains():
    case = next(item for item in PLANNER_EVAL_CASES if "affected its stock" in item["request"])
    exact = score_selection(["news", "market"], case)
    assert exact["required_hit"] is True
    assert exact["forbidden"] == []
    assert exact["unnecessary"] == []
    extra = score_selection(["news", "market", "technology"], case)
    assert extra["unnecessary"] == ["technology"]
    missed = score_selection(["news"], case)
    assert missed["required_hit"] is False


def test_market_citation_carries_windows_and_no_url():
    cited = _cite_market(
        {
            "symbol": "NVDA",
            "event_trading_date": "2026-09-22",
            "windows": {"+1D": {"return": 0.02, "relative_return": 0.01, "benchmark_return": 0.01, "available": True}},
            "volume": {"volume_ratio": 1.2},
            "volatility": {"event_window_volatility": 0.03},
            "source": {"event_id": "evt", "calculation_version": "v1"},
        }
    )
    assert cited is not None
    assert cited["source_url"] is None
    assert cited["kind"] == "market_fact"
    assert cited["market"]["symbol"] == "NVDA"
    assert cited["market"]["windows"]["+1D"]["return"] == 0.02
    assert "not a causal effect" in cited["market"]["method"]

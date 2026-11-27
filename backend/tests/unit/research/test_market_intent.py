"""Explicit market-reaction questions keep the market specialist."""

from app.research.plan import (
    accept_plan,
    fallback_plan,
    market_required,
    missing_required_agents,
    select_agents,
)
from app.research.schemas import MasterPlanPayload


def test_market_reaction_questions_require_market():
    required = [
        "What was Microsoft's market reaction?",
        "How did NVIDIA's stock react?",
        "How did Apple's shares perform after the announcement?",
        "Why did NVIDIA's stock rise after the announcement?",
        "Did the market react to Meta's announcement?",
        "How did NVIDIA perform around the recent news events in the corpus?",
    ]
    for query in required:
        assert market_required(query), query
        assert "market" in select_agents(query, {})
        assert missing_required_agents(query, ["news"]) == ["market"]


def test_company_questions_do_not_require_market():
    optional = [
        "What does Microsoft do?",
        "What is Microsoft's partnership with NVIDIA?",
        "Tell me about Microsoft's partnership with NVIDIA.",
        "Tell me about NVIDIA's business.",
    ]
    for query in optional:
        assert not market_required(query), query
        assert "market" not in select_agents(query, {})
        assert missing_required_agents(query, select_agents(query, {})) == []


def test_llm_plan_that_omits_market_is_incomplete_and_fallback_includes_it():
    query = "What was Microsoft's market reaction?"
    payload = MasterPlanPayload(
        objective="Read the articles",
        selected_agents=["news"],
        evidence_requirements=["citation_required_for_facts"],
        requested_sections=["key_facts"],
        reasoning_notes="news only",
    )
    accepted = accept_plan(payload, {"query": query}, {})
    assert accepted is not None
    assert missing_required_agents(query, accepted.selected_agents) == ["market"]
    fallback = fallback_plan({"query": query, "entity_ids": [], "event_ids": []}, {}, note="incomplete")
    assert "market" in fallback.selected_agents
    assert fallback.required_domains == ["market"]

"""Fixture routing cases. Not a measure of planner intelligence."""

from app.research.plan import ROUTING_CASES, fallback_plan, select_agents


def test_documented_requests_keep_required_and_drop_forbidden():
    for case in ROUTING_CASES:
        selected = select_agents(case["request"], {"documents": [{"title": "gpu accelerator announced"}]})
        assert set(case["required_agents"]) <= set(selected)
        assert not (set(selected) & set(case["forbidden_agents"]))
        assert len(selected) <= 4


def test_package_text_does_not_add_a_specialist():
    package = {
        "documents": [{"title": "GPU accelerator announced", "excerpt": "trading volume surged"}],
        "events": [{"title": "acquisition", "event_type": "acquisition"}],
        "entities": [{"entity_type": "technology"}],
        "graph_facts": [{"relationship": "USES"}],
        "market_facts": [{"symbol": "AMD"}],
    }
    assert select_agents("What is AMD", package) == ["company"]


def test_fallback_notes_are_short_routing_reasons():
    plan = fallback_plan(
        {"query": "How did Company X's announcement affect its stock?", "entity_ids": [], "event_ids": []},
        {},
        note="planner output rejected",
    )
    assert plan.source == "fallback"
    assert plan.selected_agents == ["news", "market"]
    assert "Market movement requested." in plan.reasoning_notes
    assert "News coverage requested." in plan.reasoning_notes
    assert len(plan.reasoning_notes) <= 500

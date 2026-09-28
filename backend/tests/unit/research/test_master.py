"""Phase 4.3 master plan, report assembly, persistence, and tool bounds."""

from __future__ import annotations

import uuid

import pytest
from app.domain.enums import ClaimKind, VerificationStatus
from app.domain.models import AgentRun, ClaimEvidence, Report, ResearchClaim, ResearchJob
from app.research.graph import get_research_graph
from app.research.llm import FakeLLMProvider
from app.research.plan import accept_plan, fallback_plan, select_agents
from app.research.report import assemble_report
from app.research.schemas import ALLOWED_SPECIALISTS, MasterPlanPayload, VerifiedClaim
from app.research.service import run_research
from app.research.tools import TOOL_PERMISSIONS, invoke_tool
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


def _claim(statement, kind, status, agent="news", cites=None):
    cites = list(cites or [])
    return VerifiedClaim(
        claim_id=str(uuid.uuid4()),
        finding_id="f1",
        statement=statement,
        claim_type=kind,
        verification_status=status,
        confidence=0.4,
        citation_ids=cites,
        evidence_refs=cites,
        agent=agent,
    )


def test_router_selects_relevant_and_skips_market():
    package = {
        "documents": [{"title": "AMD Meta partnership", "excerpt": "announced a partnership"}],
        "events": [{"title": "partnership", "event_type": "partnership"}],
        "entities": [{"entity_type": "company"}],
        "graph_facts": [{"relationship": "PARTNERED_WITH"}],
        "market_facts": [],
    }
    selected = select_agents("AMD partnership with Meta", package)
    assert "news" in selected and "company" in selected
    assert "market" not in selected
    assert set(selected) <= set(ALLOWED_SPECIALISTS)


def test_market_language_selects_market_only_when_signaled():
    selected = select_agents("window return and trading volume", {"market_facts": []})
    assert selected == ["market"]
    empty = select_agents("fog over airports", {})
    assert empty == []


def test_invalid_and_oversized_plans_fall_back():
    request = {"query": "AMD partnership announced", "entity_ids": [], "event_ids": []}
    package = {"documents": [{"title": "news", "excerpt": "announced"}], "entities": []}
    bad = MasterPlanPayload(objective="x", selected_agents=["sql_agent"], requested_sections=["key_facts"])
    assert accept_plan(bad, request, package) is None
    huge = MasterPlanPayload(
        objective="x",
        selected_agents=["news", "company", "technology", "market", "news"],
        requested_sections=["key_facts"],
    )
    assert accept_plan(huge, request, package) is None
    plan = fallback_plan(request, package, note="planner output rejected")
    assert plan.source == "fallback"
    assert set(plan.selected_agents) <= set(ALLOWED_SPECIALISTS)
    assert "sql_agent" not in plan.selected_agents


def test_fake_planner_rejects_invented_agent(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(scenario="bad_plan"),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )
    assert result.plan is not None
    assert result.plan.source == "fallback"
    assert set(result.plan.selected_agents) <= set(ALLOWED_SPECIALISTS)
    assert all(m.agent in {"master", *ALLOWED_SPECIALISTS, "evidence"} for m in result.agent_metadata)


def test_report_keeps_types_and_drops_invented_summary():
    fact = _claim("AMD partnered with Meta.", ClaimKind.FACT, VerificationStatus.SUPPORTED, "company", ["c1"])
    obs = _claim("The window return was 1.2 percent.", ClaimKind.OBSERVATION, VerificationStatus.SUPPORTED, "market", ["c2"])
    inference = _claim("The partnership may expand supply.", ClaimKind.INFERENCE, VerificationStatus.UNCERTAIN, "company", ["c1"])
    scenario = _claim("If demand rises, capacity could tighten.", ClaimKind.SCENARIO, VerificationStatus.UNCERTAIN, "technology", ["c1"])
    unsupported = _claim("Secret revenue doubled.", ClaimKind.FACT, VerificationStatus.UNSUPPORTED, "news", [])
    left = _claim("Company Alpha acquired Company Beta.", ClaimKind.FACT, VerificationStatus.CONFLICTING, "company", ["c1"])
    right = _claim("Company Alpha did not acquire Company Beta.", ClaimKind.FACT, VerificationStatus.CONFLICTING, "company", ["c3"])
    advice = _claim("Investors should buy AMD.", ClaimKind.FACT, VerificationStatus.SUPPORTED, "market", ["c2"])
    package = {"citations": [{"citation_id": "c1"}, {"citation_id": "c2"}, {"citation_id": "c3"}]}
    report, dropped = assemble_report(
        query="AMD partnership",
        claims=[fact, obs, inference, scenario, unsupported, left, right, advice],
        package=package,
        summary_bullets=["Secret revenue doubled.", "AMD partnered with Meta.", "Investors should buy AMD."],
    )
    assert dropped >= 1
    assert all(item.statement != "Secret revenue doubled." for item in report.executive_summary)
    assert report.key_facts and all(item.citation_ids for item in report.key_facts)
    assert report.market_observations and report.market_observations[0].finding_type == ClaimKind.OBSERVATION
    assert report.implications and report.implications[0].finding_type == ClaimKind.INFERENCE
    assert report.scenarios and report.scenarios[0].finding_type == ClaimKind.SCENARIO
    assert report.conflicts and len(report.conflicts) == 2
    assert "buy" not in " ".join(item.statement for item in report.executive_summary).lower()
    factual_text = " ".join(item.statement for item in (report.key_facts or []))
    assert "Secret revenue" not in factual_text
    assert "buy" not in factual_text.lower()


def test_graph_topology_is_fixed():
    graph = get_research_graph()
    names = set(graph.nodes)
    expected = {
        "validate_request",
        "build_evidence_package",
        "create_research_plan",
        "run_relevant_specialists",
        "collect_findings",
        "verify_claims",
        "resolve_conflicts",
        "assemble_report",
        "finalize_result",
    }
    assert expected <= names
    assert "sql" not in names


def test_master_has_no_tools_and_no_sql():
    assert "master" not in TOOL_PERMISSIONS
    assert "sql" not in TOOL_PERMISSIONS
    assert "cypher" not in str(TOOL_PERMISSIONS)
    denied = invoke_tool("retrieve_documents", "master", db=None)
    assert denied.errors == ["tool_not_permitted:retrieve_documents"]
    denied_sql = invoke_tool("execute_sql", "company", db=None)
    assert denied_sql.errors == ["tool_not_permitted:execute_sql"]


def test_persistence_is_idempotent_and_stores_the_report(db):
    world = seed_partnership(db)
    request = ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id])
    llm = FakeLLMProvider()
    first = run_research(db, request, llm=llm, embed_provider=FakeEmbeddingProvider(), persist=False)
    from app.research.persist import persist_result

    job_id = persist_result(db, request, first, idempotency_key="phase43-retry")
    claim_count = db.query(ResearchClaim).filter_by(research_job_id=job_id).count()
    run_count = db.query(AgentRun).filter_by(research_job_id=job_id).count()
    assert db.get(ResearchJob, job_id).status == "succeeded"
    assert claim_count > 0
    assert run_count >= 4
    assert db.query(AgentRun).filter_by(research_job_id=job_id, agent_name="master").count() == 1
    report = db.query(Report).filter_by(research_job_id=job_id).one()
    assert report.status == "final"
    assert any(section.get("section") == "report" for section in report.sections)
    second = run_research(db, request, llm=FakeLLMProvider(), embed_provider=FakeEmbeddingProvider(), persist=False)
    again = persist_result(db, request, second, idempotency_key="phase43-retry")
    assert again == job_id
    assert db.query(ResearchClaim).filter_by(research_job_id=job_id).count() == claim_count
    assert first.research_id == job_id


def test_partial_run_persists_partial_status(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(fail_agents={"news"}),
        embed_provider=FakeEmbeddingProvider(),
        persist=True,
    )
    assert result.status == "partial"
    job = db.get(ResearchJob, result.research_job_id)
    assert job.status == "partial"
    assert db.query(ResearchClaim).filter_by(research_job_id=job.id).count() >= 1


def test_claim_evidence_links_real_evidence_ids(db):
    world = seed_partnership(db)
    from app.research.persist import persist_result
    from app.research.schemas import ResearchResult

    evidence_id = str(world["evidence"].id)
    claim = _claim("AMD partnered with Meta.", ClaimKind.FACT, VerificationStatus.SUPPORTED, "company", [evidence_id])
    result = ResearchResult(
        request={"query": "AMD"},
        verified_claims=[claim],
        status="succeeded",
    )
    job_id = persist_result(db, ResearchRequest(query="AMD"), result, idempotency_key="evidence-link")
    links = db.query(ClaimEvidence).all()
    assert any(link.evidence_id == world["evidence"].id for link in links)
    assert db.query(ResearchClaim).filter_by(research_job_id=job_id).count() == 1

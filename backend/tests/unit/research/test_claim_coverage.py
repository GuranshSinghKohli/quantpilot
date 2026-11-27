"""Explicit source text versus invented relationships. Fixture reader only."""

from app.research.llm import FakeLLMProvider, _valid_findings
from app.research.schemas import AgentFindingsPayload
from app.research.service import run_research
from app.research.verify import verify_findings
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from tests.unit.research.helpers import close_db, db_session, seed_partnership

import pytest


@pytest.fixture()
def db(tmp_path):
    session = db_session(tmp_path)
    try:
        yield session
    finally:
        close_db(session)


def _tech(excerpt: str):
    return _valid_findings(
        "technology",
        {"documents": [{"excerpt": excerpt, "citation_ids": ["c1"], "title": "source"}]},
    )


def test_deployed_accelerators_are_quoted_and_not_turned_into_uses():
    findings = _tech("AMD and Meta announced a partnership to deploy Instinct accelerators in Meta data centers.")
    assert len(findings) == 1
    statement = findings[0]["statement"]
    assert findings[0]["finding_type"] == "FACT"
    assert "deploy" in statement.lower()
    assert "accelerator" in statement.lower()
    assert "documented uses" not in statement.lower()
    assert "canonical relationship" not in statement.lower()


def test_discussion_is_not_a_uses_relationship():
    findings = _tech("Company X discussed Technology Y at a conference.")
    assert findings
    assert "uses" not in findings[0]["statement"].lower()
    assert "discussed" in findings[0]["statement"].lower()


def test_co_mention_without_a_technology_term_emits_nothing():
    assert _tech("Company X and Company Y appeared in the same paragraph.") == []


def test_explicit_use_stays_a_quotation():
    findings = _tech("Company X uses accelerator Y in production.")
    assert findings
    assert "uses accelerator" in findings[0]["statement"].lower()
    assert not findings[0]["statement"].lower().startswith("documented uses")


def test_selected_technology_agent_quotes_the_accelerator_article(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="Meta data center accelerators", entity_ids=[world["meta"].entity_id]),
        llm=FakeLLMProvider(),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )
    assert result.plan is not None
    assert "technology" in result.plan.selected_agents
    statements = [item.statement.lower() for item in result.findings]
    assert any("deploy" in item and "accelerator" in item for item in statements)
    assert all("documented uses" not in item for item in statements)
    assert result.status == "succeeded"


def test_request_without_a_domain_does_not_invent_a_specialist(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="AMD Instinct Meta data centers", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )
    assert result.plan is not None
    assert result.plan.selected_agents == []
    assert result.findings == []
    assert result.status == "succeeded"


def test_selected_market_agent_with_no_bars_emits_nothing(db):
    world = seed_partnership(db)
    result = run_research(
        db,
        ResearchRequest(query="window return and trading volume", entity_ids=[world["amd"].entity_id]),
        llm=FakeLLMProvider(),
        embed_provider=FakeEmbeddingProvider(),
        persist=False,
    )
    assert result.plan is not None
    assert result.plan.selected_agents == ["market"]
    assert result.findings == []
    assert result.status == "succeeded"
    assert not any(item.get("stage") == "market" for item in result.errors)


def test_quoted_excerpt_verifies_and_bare_partnership_does_not():
    package = {
        "citations": [
            {
                "citation_id": "c1",
                "kind": "document_excerpt",
                "document_id": "d1",
                "chunk_id": "k1",
                "source_id": "s1",
                "evidence_id": "ev1",
                "title": "announcement",
                "evidence": {
                    "evidence_id": "ev1",
                    "source_id": "s1",
                    "document_id": "d1",
                    "entity_ids": [],
                },
            }
        ],
        "documents": [
            {
                "document_id": "d1",
                "chunk_id": "k1",
                "title": "announcement",
                "excerpt": "AMD and Meta announced a partnership to deploy Instinct accelerators.",
                "citation_ids": ["c1"],
            }
        ],
        "graph_facts": [],
        "events": [],
    }
    quoted = AgentFindingsPayload.model_validate(
        {"findings": _tech("AMD and Meta announced a partnership to deploy Instinct accelerators.")}
    )
    checked = verify_findings(quoted.findings, package)
    assert checked[0].verification_status.value == "supported"
    assert checked[0].claim_type.value == "FACT"
    invented = AgentFindingsPayload.model_validate(
        {
            "findings": [
                {
                    "finding_id": "x",
                    "agent": "company",
                    "statement": "Company X partnered with Company Y.",
                    "finding_type": "FACT",
                    "evidence_refs": ["c1"],
                    "citations": ["c1"],
                    "confidence": 0.9,
                    "reasoning_summary": "invented",
                }
            ]
        }
    )
    rejected = verify_findings(invented.findings, package)
    assert rejected[0].verification_status.value == "unsupported"

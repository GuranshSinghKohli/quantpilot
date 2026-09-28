"""EvidenceAgent / hallucination guard. Evidence outranks confidence."""

from __future__ import annotations

from app.domain.enums import ClaimKind, VerificationStatus
from app.research.agents import run_evidence_agent
from app.research.schemas import AgentFinding
from app.research.verify import verify_findings


def _finding(**kwargs):
    defaults = dict(
        finding_id="f1",
        agent="news",
        statement="AMD and Meta announced a partnership.",
        finding_type=ClaimKind.FACT,
        citations=["c1"],
        evidence_refs=["c1"],
        confidence=0.5,
        reasoning_summary="test",
    )
    defaults.update(kwargs)
    return AgentFinding(**defaults)


def _package():
    return {
        "documents": [
            {
                "document_id": "d1",
                "chunk_id": "ch1",
                "title": "AMD Meta partnership",
                "excerpt": "AMD and Meta announced a partnership to deploy Instinct accelerators.",
            },
            {
                "document_id": "d2",
                "chunk_id": "ch2",
                "title": "Apple Microsoft conference",
                "excerpt": "Apple and Microsoft executives spoke on separate panels. No deal was announced.",
            },
        ],
        "events": [{"event_id": "e1", "event_type": "partnership", "title": "AMD Meta partnership"}],
        "graph_facts": [
            {
                "relationship_id": "r1",
                "from_entity": "amd",
                "relationship": "PARTNERED_WITH",
                "to_entity": "meta",
                "source_id": "s1",
                "evidence_id": "ev1",
            }
        ],
        "citations": [
            {
                "citation_id": "c1",
                "kind": "graph_fact",
                "evidence_id": "ev1",
                "source_id": "s1",
                "locator": {},
            },
            {
                "citation_id": "c2",
                "kind": "document_excerpt",
                "document_id": "d2",
                "chunk_id": "ch2",
                "title": "Apple Microsoft conference",
            },
            {
                "citation_id": "c3",
                "kind": "document_excerpt",
                "document_id": "d1",
                "chunk_id": "ch1",
                "title": "AMD Meta partnership",
            },
        ],
    }


def test_valid_citation_supported():
    claims = verify_findings([_finding()], _package())
    assert claims[0].verification_status is VerificationStatus.SUPPORTED


def test_missing_and_wrong_citation():
    missing = verify_findings([_finding(citations=[], evidence_refs=[], confidence=0.99)], _package())
    assert missing[0].verification_status is VerificationStatus.UNSUPPORTED
    assert missing[0].confidence == 0.99
    wrong = verify_findings([_finding(citations=["nope"], evidence_refs=["nope"])], _package())
    assert wrong[0].verification_status is VerificationStatus.UNSUPPORTED


def test_unsupported_cooccurrence_relationship():
    finding = _finding(
        statement="Meta partnered with AMD.",
        citations=["c2"],
        evidence_refs=["c2"],
        confidence=0.91,
    )
    claims = verify_findings([finding], _package())
    assert claims[0].verification_status is VerificationStatus.UNSUPPORTED
    assert "unsupported_relationship" in claims[0].issues


def test_conflicts_preserved():
    a = _finding(finding_id="a", statement="Company Alpha acquired Company Beta.", citations=["c1"])
    b = _finding(finding_id="b", statement="Company Alpha did not acquire Company Beta.", citations=["c3"])
    claims = verify_findings([a, b], _package())
    assert {c.verification_status for c in claims} == {VerificationStatus.CONFLICTING}
    assert all(c.citation_ids for c in claims)


def test_observation_inference_scenario():
    package = _package()
    package["citations"].append(
        {"citation_id": "m1", "kind": "market_fact", "locator": {"event_id": "e1", "calculation_version": "v1"}}
    )
    obs = _finding(
        finding_id="o",
        agent="market",
        statement="The stock returned 3.2% over the +5 trading-session window.",
        finding_type=ClaimKind.OBSERVATION,
        citations=["m1"],
        evidence_refs=["m1"],
    )
    inf = _finding(
        finding_id="i",
        statement="The announcement may have increased attention around AMD technology.",
        finding_type=ClaimKind.INFERENCE,
        citations=["c1"],
        confidence=0.3,
    )
    scn = _finding(
        finding_id="s",
        statement="If the partnership results in broader deployment, demand could increase.",
        finding_type=ClaimKind.SCENARIO,
        citations=["c1"],
    )
    claims = verify_findings([obs, inf, scn], package)
    by_id = {c.finding_id: c for c in claims}
    assert by_id["o"].verification_status is VerificationStatus.SUPPORTED
    assert by_id["i"].verification_status is VerificationStatus.UNCERTAIN
    assert by_id["s"].verification_status is VerificationStatus.UNCERTAIN
    fact_as_inf = verify_findings(
        [_finding(statement="The announcement may have increased attention around AMD.")],
        package,
    )
    assert fact_as_inf[0].claim_type is ClaimKind.INFERENCE
    assert fact_as_inf[0].verification_status is not VerificationStatus.SUPPORTED


def test_low_confidence_can_still_be_verified():
    claims = verify_findings([_finding(confidence=0.1)], _package())
    assert claims[0].verification_status is VerificationStatus.SUPPORTED
    assert claims[0].confidence == 0.1


def test_investment_and_causal_market_rejected():
    buy = _finding(agent="market", statement="Investors should buy AMD after the event.", citations=["c1"])
    causal = _finding(
        agent="market",
        statement="The partnership caused the stock to return 3.2%.",
        finding_type=ClaimKind.OBSERVATION,
        citations=["c1"],
    )
    claims = verify_findings([buy, causal], _package())
    assert all(c.verification_status is VerificationStatus.UNSUPPORTED for c in claims)


def test_evidence_agent_wrapper_records_metadata():
    claims, meta = run_evidence_agent([_finding()], _package())
    assert claims
    assert meta.agent == "evidence"
    assert meta.model == "deterministic-guard"
    assert meta.prompt_version == "evidence-v1"

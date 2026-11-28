"""ResearchState serialization and transitions."""

from __future__ import annotations

import json

from app.domain.enums import ClaimKind, VerificationStatus
from app.research.schemas import AgentFinding, ResearchResult, VerifiedClaim, empty_package, initial_state


def test_initial_state_is_serializable():
    state = initial_state({"query": "AMD partnership", "entity_ids": [], "event_ids": []})
    blob = json.dumps(state, default=str)
    assert "AMD partnership" in blob
    assert state["evidence_package"]["documents"] == []
    assert state["agent_findings"] == {}


def test_empty_package_has_citation_coverage():
    package = empty_package("q")
    assert package["retrieval_metadata"]["citation_coverage"] == 1.0


def test_result_serializes_and_keeps_status_separate_from_confidence():
    result = ResearchResult(
        request={"query": "x"},
        verified_claims=[
            VerifiedClaim(
                claim_id="c1",
                finding_id="f1",
                statement="AMD is a canonical company.",
                claim_type=ClaimKind.FACT,
                verification_status=VerificationStatus.SUPPORTED,
                confidence=0.4,
                citation_ids=["cite1"],
                agent="company",
            )
        ],
        unsupported_claims=[
            VerifiedClaim(
                claim_id="c2",
                finding_id="f2",
                statement="Secret acquisition.",
                claim_type=ClaimKind.FACT,
                verification_status=VerificationStatus.UNSUPPORTED,
                confidence=0.99,
                agent="news",
                issues=["missing_citation"],
            )
        ],
    )
    payload = json.loads(result.model_dump_json())
    assert payload["verified_claims"][0]["confidence"] == 0.4
    assert payload["unsupported_claims"][0]["verification_status"] == "unsupported"
    assert payload["unsupported_claims"][0]["confidence"] == 0.99


def test_finding_rejects_unknown_type():
    import pytest

    with pytest.raises(Exception):
        AgentFinding(
            finding_id="x",
            agent="news",
            statement="hi",
            finding_type="RUMOR",  # type: ignore[arg-type]
        )

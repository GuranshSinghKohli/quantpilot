"""Document facts need a real evidence record. A citation id is not one."""

from app.domain.enums import ClaimKind, VerificationStatus
from app.research.schemas import AgentFinding
from app.research.verify import verify_findings

_SOURCE = "00000000-0000-0000-0000-0000000000a1"
_OTHER = "00000000-0000-0000-0000-0000000000b2"
_EVIDENCE = "00000000-0000-0000-0000-0000000000c3"
_MSFT = "a6d84c30-9814-454f-99a3-66e1cdeaf237"
_AAPL = "2c509dc4-bf12-4c8f-ae08-4b96025cfe51"


def _finding(**kwargs):
    defaults = dict(
        finding_id="f1",
        agent="news",
        statement="Microsoft reported Intelligent Cloud revenue growth in the cited article.",
        finding_type=ClaimKind.FACT,
        citations=["c1"],
        evidence_refs=["c1"],
        confidence=0.9,
        reasoning_summary="test",
    )
    defaults.update(kwargs)
    return AgentFinding(**defaults)


def _package(*, evidence_id=None, evidence=None, entity_id=None):
    citation = {
        "citation_id": "c1",
        "kind": "document_excerpt",
        "document_id": "d1",
        "chunk_id": "ch1",
        "source_id": _SOURCE,
        "source_url": "https://example.com/msft",
        "title": "source",
    }
    if evidence_id:
        citation["evidence_id"] = evidence_id
    if evidence is not None:
        citation["evidence"] = evidence
    provenance = {"entity_id": entity_id} if entity_id else None
    return {
        "documents": [
            {
                "document_id": "d1",
                "chunk_id": "ch1",
                "title": "source",
                "excerpt": "Microsoft reported Intelligent Cloud revenue growth in the cited article.",
                "source_url": "https://example.com/msft",
            }
        ],
        "citations": [citation],
        "entities": [],
    }, provenance


def test_document_observation_with_empty_evidence_id_is_not_supported():
    package, _ = _package()
    claim = verify_findings(
        [_finding(finding_type=ClaimKind.OBSERVATION, agent="company")],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "missing_evidence" in claim.issues
    assert claim.evidence_ids == []


def test_supported_fact_with_empty_evidence_id_is_not_supported():
    package, _ = _package()
    claim = verify_findings([_finding()], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "missing_evidence" in claim.issues
    assert claim.evidence_ids == []


def test_evidence_for_a_different_source_is_not_supported():
    package, _ = _package(
        evidence_id=_EVIDENCE,
        evidence={
            "evidence_id": _EVIDENCE,
            "source_id": _OTHER,
            "document_id": "d1",
            "entity_ids": [],
        },
    )
    claim = verify_findings([_finding()], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "evidence_source_mismatch" in claim.issues


def test_evidence_for_the_right_source_and_wrong_entity_is_not_supported():
    package, provenance = _package(
        evidence_id=_EVIDENCE,
        evidence={
            "evidence_id": _EVIDENCE,
            "source_id": _SOURCE,
            "document_id": "d1",
            "entity_ids": [_AAPL],
        },
        entity_id=_MSFT,
    )
    claim = verify_findings([_finding(provenance=provenance)], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "evidence_entity_mismatch" in claim.issues


def test_matching_evidence_record_can_support_the_fact():
    package, provenance = _package(
        evidence_id=_EVIDENCE,
        evidence={
            "evidence_id": _EVIDENCE,
            "source_id": _SOURCE,
            "document_id": "d1",
            "entity_ids": [_MSFT],
        },
        entity_id=_MSFT,
    )
    claim = verify_findings([_finding(provenance=provenance)], package)[0]
    assert claim.verification_status is VerificationStatus.SUPPORTED
    assert claim.evidence_ids == [_EVIDENCE]
    assert claim.source_ids == [_SOURCE]

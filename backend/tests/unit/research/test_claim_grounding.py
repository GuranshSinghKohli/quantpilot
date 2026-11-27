"""Phase 12 failures: wrong security, wrong unit, mixed windows, inserted entities, overclaim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.domain.enums import ClaimKind, EventEntityRole, RelationshipType, VerificationStatus
from app.domain.models import EventEntity
from app.domain.schemas import CompanyIn, EvidenceIn
from app.graph.candidates import CandidateProposal, RelationshipCandidateService
from app.graph.errors import EvidenceInsufficient
from app.graph.resolve import EntityRef
from app.market.binding import invariant_issues
from app.market.render import format_return_percent, render_market_statement
from app.repositories import entities
from app.research.agents import run_specialist
from app.research.audit_regression import disposition
from app.research.llm import FakeLLMProvider
from app.research.schemas import AgentFinding
from app.research.verify import verify_findings
from app.retrieval.package import ResearchRequest, assemble_package
from tests.unit.research.test_ticker_binding import confirmed_event, impact, listed
from tests.unit.retrieval.helpers import close_db, migrated_db


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)

AUDIT = Path(__file__).resolve().parents[4] / "eval" / "human_claim_audit_v1.json"
svc = RelationshipCandidateService()

MSFT_RETURN = 0.015857264368747126
NVDA_RETURN = 0.022990057137715292
AAPL_RETURN = 0.008478862344926119
BENCH = 0.01550499547059836
META_RETURN = 0.01336679983959832
META_BENCH = 0.01133877063855171
OTHER_NVDA = 0.01335825658794576
OTHER_BENCH = -0.0011932861264096761


def _window(asset, benchmark, relative=None):
    return {
        "return": asset,
        "benchmark_return": benchmark,
        "relative_return": relative if relative is not None else asset - benchmark,
        "available": True,
    }


def _fact(entity_id, symbol, name, event_id, asset, benchmark, relative=None):
    return {
        "entity_id": entity_id,
        "symbol": symbol,
        "canonical_name": name,
        "event_id": event_id,
        "benchmark_symbol": "SPY",
        "calculation_version": "v1",
        "windows": {"+1D": _window(asset, benchmark, relative)},
    }


def _package(fact, securities):
    market = {
        "entity_id": fact["entity_id"],
        "symbol": fact["symbol"],
        "canonical_name": fact["canonical_name"],
        "event_id": fact["event_id"],
        "benchmark_symbol": "SPY",
        "configured_benchmark_symbol": "SPY",
        "calculation_version": "v1",
        "windows": fact["windows"],
        "identity_verified": True,
    }
    return {
        "resolved_securities": securities,
        "market_facts": [fact],
        "entities": [],
        "documents": [],
        "citations": [
            {
                "citation_id": "m1",
                "kind": "market_fact",
                "source_url": None,
                "market": market,
            }
        ],
    }


def _finding(statement, kind=ClaimKind.OBSERVATION, citations=None, agent="market"):
    return AgentFinding(
        finding_id="f1",
        agent=agent,
        statement=statement,
        finding_type=kind,
        citations=citations or ["m1"],
        evidence_refs=citations or ["m1"],
        confidence=0.9,
        reasoning_summary="test",
    )


def test_fraction_renders_as_percent_not_the_raw_value():
    assert format_return_percent(MSFT_RETURN) == "+1.59%"
    assert format_return_percent(NVDA_RETURN) == "+2.30%"
    assert format_return_percent(BENCH) == "+1.55%"
    assert format_return_percent(META_RETURN) == "+1.34%"
    assert format_return_percent(META_BENCH) == "+1.13%"
    assert format_return_percent(AAPL_RETURN) == "+0.85%"
    fact = _fact("msft", "MSFT", "Microsoft", "evt-1", MSFT_RETURN, BENCH)
    statement = render_market_statement(fact, "+1D")
    assert statement == (
        "Microsoft (MSFT) returned +1.59% over the +1 session window, versus +1.55% for SPY."
    )
    assert "0.0159%" not in statement


def test_hca11_nvda_impact_rejected_for_microsoft_question(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, "QQQ column", microsoft)
    db.add(
        EventEntity(
            event_id=event.id,
            entity_id=nvidia.entity_id,
            role=EventEntityRole.OTHER.value,
        )
    )
    db.flush()
    impact(db, event, microsoft, "MSFT", MSFT_RETURN)
    impact(db, event, nvidia, "NVDA", NVDA_RETURN)
    package = assemble_package(
        db, ResearchRequest(query="What recent news about Microsoft could be relevant to its market performance?")
    )
    assert [fact["symbol"] for fact in package["market_facts"]] == ["MSFT"]
    assert package["market_facts"][0]["entity_id"] == str(microsoft.entity_id)
    nvda = _fact(str(nvidia.entity_id), "NVDA", "NVIDIA", str(event.id), NVDA_RETURN, BENCH)
    msft = package["market_facts"][0]
    cite = next(item["market"] for item in package["citations"] if item["kind"] == "market_fact")
    assert invariant_issues(nvda, cite, package["resolved_securities"])
    assert invariant_issues(msft, cite, package["resolved_securities"]) == []
    cite_id = next(item["citation_id"] for item in package["citations"] if item["kind"] == "market_fact")
    forged = _finding(
        "The stock returned 1.59% over the +1 trading-session window and the benchmark return was 1.55%.",
        citations=[cite_id],
    )
    claim = verify_findings([forged], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "market_number_mismatch" in claim.issues


def test_hca28_wrong_security_number_is_rejected():
    fact = _fact("nvda", "NVDA", "NVIDIA", "evt-qqq", NVDA_RETURN, BENCH)
    package = _package(fact, [{"entity_id": "nvda", "symbol": "NVDA"}])
    forged = _finding("The stock returned 0.0085% over the +1 trading-session window and the benchmark return is 0.0155%.")
    claim = verify_findings([forged], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    rendered = render_market_statement(fact, "+1D")
    assert rendered.startswith("NVIDIA (NVDA) returned +2.30%")
    assert "0.0085%" not in rendered


def test_hca29_unit_error_is_replaced_by_the_renderer():
    fact = _fact("meta", "META", "Meta Platforms", "evt-meta", META_RETURN, META_BENCH)
    package = _package(fact, [{"entity_id": "meta", "symbol": "META"}])
    forged = _finding("The stock returned 0.0134% over the +1 trading-session window and the benchmark return is 0.0113%.")
    rejected = verify_findings([forged], package)[0]
    assert rejected.verification_status is VerificationStatus.UNSUPPORTED
    rendered = verify_findings(
        [_finding("Meta Platforms (META) returned +1.34% over the +1 session window, versus +1.13% for SPY.")],
        package,
    )[0]
    assert rendered.verification_status is VerificationStatus.SUPPORTED
    assert rendered.provenance is None or rendered.entity_id in (None, "meta")


def test_hca31_mixed_windows_do_not_match_either_event():
    first = _fact("nvda", "NVDA", "NVIDIA", "evt-a", NVDA_RETURN, BENCH)
    second = _fact("nvda", "NVDA", "NVIDIA", "evt-b", OTHER_NVDA, OTHER_BENCH)
    mixed = "NVIDIA (NVDA) returned +1.34% over the +1 session window, versus +1.55% for SPY."
    assert render_market_statement(first, "+1D") != mixed
    assert render_market_statement(second, "+1D") != mixed
    package = _package(first, [{"entity_id": "nvda", "symbol": "NVDA"}])
    claim = verify_findings([_finding(mixed)], package)[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "market_number_mismatch" in claim.issues


def test_entity_and_symbol_and_event_mismatch_are_rejected():
    impact_row = _fact("msft", "MSFT", "Microsoft", "evt-1", MSFT_RETURN, BENCH)
    securities = [{"entity_id": "msft", "symbol": "MSFT"}]
    wrong_symbol = {**impact_row, "symbol": "NVDA"}
    wrong_entity = {**impact_row, "entity_id": "nvda"}
    wrong_event = {**impact_row, "event_id": "evt-2"}
    assert "symbol_mismatch" in invariant_issues(impact_row, wrong_symbol, securities)
    assert "entity_mismatch" in invariant_issues(impact_row, wrong_entity, securities)
    assert "event_mismatch" in invariant_issues(impact_row, wrong_event, securities)
    assert invariant_issues(impact_row, impact_row, securities) == []


def test_specialist_renders_msft_percent_and_drops_model_rewrite(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, "Microsoft cloud", microsoft)
    impact(db, event, microsoft, "MSFT", MSFT_RETURN)
    impact(db, event, nvidia, "NVDA", NVDA_RETURN)
    package = assemble_package(db, ResearchRequest(query="How did Microsoft trade?"))
    findings, _meta, err = run_specialist("market", package, provider=FakeLLMProvider())
    assert err is None
    statements = [item.statement for item in findings]
    assert any(item.startswith("Microsoft (MSFT) returned +1.59%") for item in statements)
    assert all("NVDA" not in item for item in statements)
    assert all("0.0159%" not in item for item in statements)
    claims = verify_findings(findings, package)
    supported = [item for item in claims if item.verification_status is VerificationStatus.SUPPORTED]
    assert supported
    assert supported[0].entity_id == str(microsoft.entity_id)
    assert supported[0].provenance["window"] == "+1D"
    assert supported[0].provenance["symbol"] == "MSFT"
    assert supported[0].provenance["event_id"] == str(event.id)
    assert supported[0].provenance["asset_return"] == pytest.approx(MSFT_RETURN)
    assert supported[0].citation_ids


def _doc_package(statement_entities, excerpt, citation_id="c1"):
    return {
        "entities": statement_entities,
        "documents": [{"document_id": "d1", "chunk_id": "ch1", "title": "source", "excerpt": excerpt}],
        "citations": [
            {
                "citation_id": citation_id,
                "kind": "document_excerpt",
                "document_id": "d1",
                "chunk_id": "ch1",
                "source_id": "00000000-0000-0000-0000-000000000001",
                "evidence_id": "00000000-0000-0000-0000-000000000002",
                "title": "source",
                "evidence": {
                    "evidence_id": "00000000-0000-0000-0000-000000000002",
                    "source_id": "00000000-0000-0000-0000-000000000001",
                    "document_id": "d1",
                    "entity_ids": [],
                },
            }
        ],
    }


def test_berkshire_article_rejects_inserted_microsoft():
    excerpt = (
        "31% of Berkshire Hathaway's portfolio is riding on these 2 AI stocks. "
        "Alphabet is the growth name. Apple claims just over 20% of the portfolio."
    )
    package = _doc_package(
        [
            {"canonical_name": "Microsoft Corporation", "legal_name": "Microsoft Corporation", "ticker": "MSFT"},
            {"canonical_name": "Apple Inc.", "legal_name": "Apple Inc.", "ticker": "AAPL"},
        ],
        excerpt,
    )
    claim = verify_findings(
        [
            _finding(
                "Berkshire Hathaway has concentrated approximately 31% of its stock portfolio in AI-focused companies, including Microsoft.",
                kind=ClaimKind.FACT,
                agent="company",
                citations=["c1"],
            )
        ],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "entity_not_in_source" in claim.issues


def test_robotics_article_without_microsoft_rejects_microsoft():
    excerpt = "The company is already behind in the cybercab race, with Alphabet's Waymo leading the way."
    package = _doc_package(
        [{"canonical_name": "Microsoft Corporation", "legal_name": "Microsoft Corporation", "ticker": "MSFT"}],
        excerpt,
    )
    claim = verify_findings(
        [
            _finding(
                "Microsoft is mentioned in the context of AI alongside NVIDIA and Alphabet.",
                kind=ClaimKind.FACT,
                agent="company",
                citations=["c1"],
            )
        ],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "entity_not_in_source" in claim.issues


def test_azure_is_not_intelligent_cloud_growth():
    excerpt = (
        "Microsoft's Intelligent Cloud results include Azure, its cloud computing platform. "
        "Intelligent Cloud revenue came in at $39.3 billion, beating our consensus estimate handily "
        "and growing at an accelerated rate of 32% YoY."
    )
    package = _doc_package(
        [{"canonical_name": "Microsoft Corporation", "legal_name": "Microsoft Corporation", "ticker": "MSFT"}],
        excerpt,
    )
    claim = verify_findings(
        [
            _finding(
                "Microsoft's Azure cloud service grew 32% year-over-year.",
                kind=ClaimKind.FACT,
                agent="company",
                citations=["c1"],
            )
        ],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "scope_changed" in claim.issues


def test_one_analyst_is_not_analysts():
    excerpt = "Analyst Simon Leopold at Raymond James expects Nvidia to reach $515 per share."
    package = _doc_package(
        [{"canonical_name": "NVIDIA Corporation", "legal_name": "NVIDIA Corporation", "ticker": "NVDA"}],
        excerpt,
    )
    claim = verify_findings(
        [
            _finding(
                "Nvidia could reach $515 per share according to analysts.",
                kind=ClaimKind.INFERENCE,
                agent="news",
                citations=["c1"],
            )
        ],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "population_widened" in claim.issues


def test_sourced_hold_rating_stays_excluded():
    excerpt = "Oracle is sporting a Zacks Rank of #2 (Buy), while Microsoft has a Zacks Rank of #3 (Hold)."
    package = _doc_package(
        [{"canonical_name": "Microsoft Corporation", "legal_name": "Microsoft Corporation", "ticker": "MSFT"}],
        excerpt,
    )
    claim = verify_findings(
        [
            _finding(
                "Microsoft is rated as a 'Hold' by Zacks, while Oracle is rated as a 'Buy'.",
                kind=ClaimKind.FACT,
                agent="company",
                citations=["c1"],
            )
        ],
        package,
    )[0]
    assert claim.verification_status is VerificationStatus.UNSUPPORTED
    assert "investment_language" in claim.issues


def test_explicit_partnership_and_relationship_can_be_candidates(db):
    apple, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Apple Inc.", ticker="AAPL", cik="320193"))
    nvidia, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810")
    )
    amd, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="Advanced Micro Devices Inc.", ticker="AMD", cik="796343")
    )
    meta, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="Meta Platforms Inc.", ticker="META", cik="1326801")
    )
    from tests.unit.graph.test_candidates import _evidence

    apple_src, apple_ev = _evidence(
        db,
        "In its proposed partnership with Nvidia, Apple would use Nvidia's NVLink Fusion.",
        "https://example.com/apple-nvidia-proposed",
    )
    row, created = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=apple.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=nvidia.entity_id),
            source_id=apple_src.id,
            evidence_id=apple_ev.id,
            confidence=0.7,
        ),
    )
    assert created
    assert row.relationship_type == RelationshipType.PARTNERED_WITH.value
    assert row.source_id == apple_src.id
    assert row.evidence_id == apple_ev.id
    amd_src, amd_ev = _evidence(
        db,
        "Strategic relationships with OpenAI, Meta and Anthropic also provide greater visibility.",
        "https://example.com/amd-relationships",
    )
    amd_row, amd_created = svc.propose(
        db,
        CandidateProposal(
            from_entity=EntityRef(entity_id=amd.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=meta.entity_id),
            source_id=amd_src.id,
            evidence_id=amd_ev.id,
            confidence=0.7,
        ),
    )
    assert amd_created
    assert amd_row.provenance_kind
    discussed_src, discussed_ev = _evidence(
        db,
        "Microsoft and NVIDIA discussed AI infrastructure.",
        "https://example.com/discussed-only",
    )
    with pytest.raises(EvidenceInsufficient):
        svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(entity_id=apple.entity_id),
                relationship_type=RelationshipType.PARTNERED_WITH,
                to_entity=EntityRef(entity_id=nvidia.entity_id),
                source_id=discussed_src.id,
                evidence_id=discussed_ev.id,
                confidence=0.4,
            ),
        )


def test_phase12_audit_dispositions_are_recorded():
    doc = json.loads(AUDIT.read_text())
    rows = []
    for case in doc["cases"]:
        rows.append(
            {
                "evaluation_id": case["evaluation_id"],
                "disposition": disposition(case),
                "error_types": case.get("error_types") or [],
                "quantpilot_status": case["quantpilot_status"],
            }
        )
    prevented = {row["evaluation_id"] for row in rows if row["disposition"] == "PREVENTS"}
    detected = {row["evaluation_id"] for row in rows if row["disposition"] == "DETECTS"}
    assert {"hca-11", "hca-28", "hca-29", "hca-31", "hca-33"} <= prevented
    assert {"hca-16", "hca-17", "hca-09", "hca-26"} <= detected
    assert disposition({"evaluation_id": "hca-14", "error_types": ["other"]}) == "STILL ALLOWS"
    out = Path(__file__).resolve().parents[4] / "eval" / "phase13_grounding_results.json"
    out.write_text(
        json.dumps(
            {
                "dataset_version": "human-claim-audit-v1",
                "phase": "13",
                "note": "Disposition of each Phase 12 case under the new checks. Historical reports were not rewritten.",
                "cases": rows,
            },
            indent=2,
        )
        + "\n"
    )

"""Market windows stay on the resolved security. NVDA cannot explain Microsoft."""

from __future__ import annotations

import json

import pytest
from app.domain.enums import ClaimKind, EventEntityRole, EventStatus, EventType
from app.domain.models import EventEntity, EventImpact
from app.domain.schemas import CompanyIn, EventEntityIn, EventIn, EvidenceIn
from app.market.binding import REJECTED_LIMITATION, UNVERIFIED_LIMITATION, market_binding_status
from app.repositories import entities, events, sources
from app.research.llm import FakeLLMProvider
from app.research.report import assemble_report
from app.research.schemas import ReportItem, ResearchReport
from app.research.security import resolve_securities, validate_report_market_bindings
from app.research.service import run_research
from app.research.tools import invoke_tool
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest, assemble_package
from tests.unit.retrieval.helpers import NOW, close_db, migrated_db, news_source


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def listed(db, name, ticker, cik):
    row, _ = entities.get_or_create_company(
        db,
        CompanyIn(legal_name=name, ticker=ticker, cik=cik, exchange="NASDAQ"),
    )
    return row


def confirmed_event(db, title, company=None):
    src = news_source(db, f"https://example.com/{title.replace(' ', '-').lower()}", title, title)
    evidence, _ = sources.add_evidence(
        db,
        src.id,
        EvidenceIn(excerpt=title, extraction_method="test", extracted_at=NOW),
    )
    links = []
    if company is not None:
        links.append(
            EventEntityIn(
                entity_id=company.entity_id,
                role=EventEntityRole.SUBJECT,
                source_id=src.id,
                evidence_id=evidence.id,
            )
        )
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=EventType.NEWS,
            title=title,
            detected_at=NOW,
            occurred_at=NOW,
            published_at=NOW,
            primary_source_id=src.id,
            evidence_id=evidence.id,
            entities=links,
        ),
    )
    event.status = EventStatus.CONFIRMED.value
    db.flush()
    return event


def impact(db, event, company, symbol, ret=0.0182):
    row = EventImpact(
        event_id=event.id,
        entity_id=company.entity_id,
        symbol=symbol,
        return_p1=ret,
        relative_return_p1=ret,
        benchmark_return_p1=0.001,
        calculation_version="v1",
        calculated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def symbols(package):
    return [fact["symbol"] for fact in package["market_facts"]]


def test_microsoft_query_returns_only_msft(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    msft_event = confirmed_event(db, "Microsoft dividend", microsoft)
    nvda_event = confirmed_event(db, "NVIDIA shareholder return", nvidia)
    impact(db, msft_event, microsoft, "MSFT", 0.0182)
    impact(db, nvda_event, nvidia, "NVDA", 0.04)
    package = assemble_package(db, ResearchRequest(query="What happened to Microsoft?"))
    assert [row["symbol"] for row in package["resolved_securities"]] == ["MSFT"]
    assert package["resolved_securities"][0]["entity_id"] == str(microsoft.entity_id)
    assert package["resolved_securities"][0]["symbol"] == microsoft.ticker
    assert symbols(package) == ["MSFT"]
    assert all(fact["entity_id"] == str(microsoft.entity_id) for fact in package["market_facts"])
    cite = next(item for item in package["citations"] if item["kind"] == "market_fact")
    assert cite["source_url"] is None
    assert cite["market"]["symbol"] == "MSFT"
    assert cite["market"]["canonical_name"] == "Microsoft"
    assert cite["market"]["configured_benchmark_symbol"] == "SPY"
    assert cite["market"]["entity_id"] == str(microsoft.entity_id)
    assert cite["market"]["calculation_version"] == "v1"
    assert cite["market"]["event_id"] == str(msft_event.id)


def test_nvidia_query_returns_only_nvda(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    impact(db, confirmed_event(db, "Microsoft dividend", microsoft), microsoft, "MSFT")
    impact(db, confirmed_event(db, "NVIDIA shareholder return", nvidia), nvidia, "NVDA")
    package = assemble_package(db, ResearchRequest(query="What happened to NVIDIA?"))
    assert symbols(package) == ["NVDA"]
    assert package["resolved_securities"][0]["entity_id"] == str(nvidia.entity_id)


def test_microsoft_event_rejects_nvda_impact(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, "Microsoft event", microsoft)
    impact(db, event, nvidia, "NVDA", 0.09)
    package = assemble_package(db, ResearchRequest(query="What happened to Microsoft?"))
    assert package["market_facts"] == []
    forged = ResearchReport(
        title="Microsoft",
        market_observations=[
            ReportItem(
                statement="NVDA returned 0.09.",
                finding_type=ClaimKind.OBSERVATION,
                claim_id="obs",
                citation_ids=["bad"],
                agent="market",
            )
        ],
        citations=[
            {
                "citation_id": "bad",
                "kind": "market_fact",
                "source_url": None,
                "market": {
                    "entity_id": str(nvidia.entity_id),
                    "symbol": "NVDA",
                    "identity_verified": True,
                },
            }
        ],
        metadata={"resolved_securities": package["resolved_securities"]},
    )
    cleaned = validate_report_market_bindings(forged)
    assert cleaned.citations == []
    assert cleaned.market_observations is None
    assert REJECTED_LIMITATION in cleaned.limitations
    assert "NVDA" not in json.dumps(cleaned.model_dump(mode="json"))


def test_microsoft_event_accepts_msft_impact(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, "Microsoft event", microsoft)
    impact(db, event, microsoft, "MSFT", 0.0182)
    impact(db, event, nvidia, "NVDA", 0.09)
    package = assemble_package(db, ResearchRequest(query="What was Microsoft's market reaction?"))
    assert symbols(package) == ["MSFT"]
    assert package["market_facts"][0]["entity_id"] == str(microsoft.entity_id)


def test_same_entity_wrong_symbol_is_rejected(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    securities, _notes = resolve_securities(db, "Microsoft", [])
    assert securities[0]["symbol"] == "MSFT"
    assert securities[0]["symbol"] == microsoft.ticker
    status = market_binding_status(
        {"entity_id": str(microsoft.entity_id), "symbol": "NVDA"},
        securities,
    )
    assert status == "symbol_mismatch"
    event = confirmed_event(db, "Microsoft event", microsoft)
    impact(db, event, microsoft, "NVDA", 0.09)
    package = assemble_package(db, ResearchRequest(query="Microsoft"))
    assert package["market_facts"] == []


def test_same_symbol_wrong_entity_is_rejected(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    securities, _notes = resolve_securities(db, "Microsoft", [])
    status = market_binding_status(
        {"entity_id": str(nvidia.entity_id), "symbol": "MSFT"},
        securities,
    )
    assert status == "entity_mismatch"
    event = confirmed_event(db, "Microsoft event", microsoft)
    impact(db, event, nvidia, "MSFT", 0.02)
    package = assemble_package(db, ResearchRequest(query="Microsoft"))
    assert package["market_facts"] == []


def test_unresolved_security_returns_no_market_impact(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    event = confirmed_event(db, "Microsoft dividend", microsoft)
    impact(db, event, microsoft, "MSFT")
    package = assemble_package(db, ResearchRequest(query="What happened to Contoso?"))
    assert package["resolved_securities"] == []
    assert package["market_facts"] == []
    report, _dropped = assemble_report(query="What happened to Contoso?", claims=[], package=package)
    assert report.market_observations in (None, [])
    assert UNVERIFIED_LIMITATION in report.limitations
    tool = invoke_tool("get_market_impact", "market", db, event_ids=[event.id])
    assert tool.data == []
    assert tool.errors == ["security_identity_unverified"]


def test_co_mention_does_not_select_a_market_security(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, "Microsoft OpenAI and NVIDIA announce a partnership")
    impact(db, event, microsoft, "MSFT", 0.01)
    impact(db, event, nvidia, "NVDA", 0.08)
    request = ResearchRequest(
        query="Microsoft OpenAI and NVIDIA announce a partnership",
        event_ids=[event.id],
    )
    unbound = assemble_package(db, request)
    assert unbound["market_facts"] == []
    db.add(
        EventEntity(
            event_id=event.id,
            entity_id=microsoft.entity_id,
            role=EventEntityRole.SUBJECT.value,
        )
    )
    db.flush()
    bound = assemble_package(db, request)
    assert symbols(bound) == ["MSFT"]
    assert all(fact["entity_id"] == str(microsoft.entity_id) for fact in bound["market_facts"])


def test_end_to_end_queries_keep_one_security(db):
    microsoft = listed(db, "Microsoft Corporation", "MSFT", "789019")
    nvidia = listed(db, "NVIDIA Corporation", "NVDA", "1045810")
    impact(db, confirmed_event(db, "Microsoft dividend announcement", microsoft), microsoft, "MSFT", 0.0182)
    impact(db, confirmed_event(db, "NVIDIA shareholder return announcement", nvidia), nvidia, "NVDA", 0.04)
    cases = [
        ("What happened to Microsoft?", "MSFT", False),
        ("What happened to NVIDIA?", "NVDA", False),
        ("What was Microsoft's market reaction to the dividend announcement?", "MSFT", True),
        ("What was NVIDIA's market reaction to the shareholder return announcement?", "NVDA", True),
    ]
    records = []
    for query, expected, expect_report in cases:
        package = assemble_package(db, ResearchRequest(query=query), provider=FakeEmbeddingProvider())
        result = run_research(
            db,
            ResearchRequest(query=query),
            llm=FakeLLMProvider(),
            embed_provider=FakeEmbeddingProvider(),
            persist=True,
        )
        report_symbols = [
            cite["market"]["symbol"]
            for cite in result.report.citations
            if cite.get("kind") == "market_fact"
        ]
        record = {
            "query": query,
            "resolved_entity_id": [row["entity_id"] for row in package["resolved_securities"]],
            "resolved_symbol": [row["symbol"] for row in package["resolved_securities"]],
            "market_entity_id": [fact["entity_id"] for fact in package["market_facts"]],
            "market_symbol": [fact["symbol"] for fact in package["market_facts"]],
            "report_symbol": report_symbols,
        }
        records.append(record)
        assert record["resolved_symbol"] == [expected]
        assert record["market_symbol"] == [expected]
        assert set(record["market_entity_id"]) == {record["resolved_entity_id"][0]}
        assert expected in record["market_symbol"]
        other = "NVDA" if expected == "MSFT" else "MSFT"
        assert other not in record["market_symbol"]
        assert other not in record["report_symbol"]
        blob = json.dumps(result.report.model_dump(mode="json"))
        assert other not in blob
        if expect_report:
            assert report_symbols == [expected]
            assert result.report.market_observations
    assert json.loads(json.dumps(records)) == records

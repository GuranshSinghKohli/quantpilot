"""Phase 5 — evidence graph and auditable evidence intelligence."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase5.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_GRAPH_ENABLED"] = "true"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    ClaimCreateRequest,
    ClaimEvidenceLinkRequest,
    EvidenceCreateRequest,
    InvestigationCreateRequest,
)
from app.services import evidence_graph, evidence_ledger_store  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()
client = TestClient(app)
OWNER = "guest:phase5guestaaaaaaaaaaaaaaaaaaaa"
GUEST = {"X-Guest-Id": "phase5guestaaaaaaaaaaaaaaaaaaaa"}
EVENT_TIME = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)


def _item(
    source_type: str,
    title: str,
    excerpt: str = "",
    url: str = "",
    published: str | None = None,
    extra: dict | None = None,
    retrieval_method: str = "mcp",
) -> dict:
    payload = dict(extra or {})
    if published is not None:
        payload["date"] = published
    return {
        "source_type": source_type,
        "retrieval_method": retrieval_method,
        "title": title,
        "excerpt": excerpt or title,
        "source_url": url,
        "raw_payload": payload,
    }


def _nvda_release() -> dict:
    return _item(
        "ir_page",
        "NVIDIA Q2 FY2027 Earnings Release",
        "Gross margin guidance declined. Next-quarter outlook lowered.",
        url="https://investor.nvidia.com/q2-fy2027",
        published="2026-08-19",
        extra={"publisher": "NVIDIA Investor Relations"},
    )


def _nvda_transcript() -> dict:
    return _item(
        "transcript",
        "NVIDIA Q2 earnings transcript",
        "Management discussed lower gross-margin guidance for the next quarter.",
        url="https://example.com/transcript/nvda-q2",
        published="2026-08-20",
    )


def _revenue_news() -> dict:
    return _item(
        "news",
        "NVIDIA revenue beat estimates",
        "Revenue increased 12% and beat estimates. Strong revenue result.",
        url="https://www.reuters.com/nvda-beat",
        published="2026-08-20",
    )


def _sector_price() -> dict:
    return _item(
        "price",
        "SOX semiconductor sector decline",
        "Semiconductor sector declined with NVDA.",
        url="",
        published="2026-08-20",
        retrieval_method="system",
    )


def _move() -> MoveSnapshot:
    return MoveSnapshot(
        ticker="NVDA",
        move_pct=-8.7,
        window_label="1d",
        current_price=100.0,
        previous_close=109.5,
        is_noise=False,
        source="test",
        detail="NVDA fell 8.7%",
        residual_pct=-7.7,
        move_zscore=-4.0,
        depth="deep",
    )


def _create_case(db, ticker: str = "NVDA"):
    return evidence_ledger_store.create_investigation(
        db,
        owner_key=OWNER,
        user=None,
        body=InvestigationCreateRequest(
            ticker=ticker,
            trigger_reason="on_demand",
            summary=f"Investigating {ticker}.",
        ),
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_creation_and_retrieval_provenance():
    db = SessionLocal()
    try:
        created = _create_case(db)
        item = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="ir_page",
                retrieval_method="mcp",
                title="NVIDIA Q2 FY2027 Earnings Release",
                excerpt="Gross margin guidance declined.",
                source_url="https://investor.nvidia.com/q2-fy2027",
            ),
        )
        assert item is not None
        assert item.source_type == "ir_page"
        assert item.source_taxonomy == "PRIMARY_COMPANY"
        assert item.source_class == "PRIMARY"
        assert item.source_quality == "PRIMARY"
        assert item.source_tier == "TIER_1"
        assert item.content_hash
        assert item.source_name
        assert item.retrieved_at is not None
        assert item.source_url.startswith("https://investor.nvidia.com")
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        assert detail.evidence_count == 1
        assert detail.evidence_items[0].id == item.id
        assert detail.evidence_items[0].content_hash == item.content_hash
    finally:
        db.close()


def test_content_hash_is_stable_and_ignores_metadata():
    a = evidence_graph.content_hash("Title", "  Gross margin GUIDANCE declined. ")
    b = evidence_graph.content_hash("other title", "Gross margin guidance declined.")
    assert a == b
    different = evidence_graph.content_hash("Title", "Revenue increased 12%.")
    assert a != different


def test_duplicate_content_same_url_is_dropped():
    body = _nvda_release()
    reprint = dict(body)
    reprint["raw_payload"] = {"publisher": "Cache", "date": "2026-08-21"}
    kept, dropped = evidence_graph.dedupe_items([body, reprint])
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0]["content_hash"]


def test_same_content_different_url_is_grouped_not_dropped():
    primary = _nvda_release()
    wire = dict(primary)
    wire["source_type"] = "news"
    wire["source_url"] = "https://www.reuters.com/reprint"
    wire["title"] = "NVIDIA Q2 FY2027 Earnings Release"
    wire["excerpt"] = primary["excerpt"]
    kept, dropped = evidence_graph.dedupe_items([primary, wire])
    assert dropped == 0
    assert len(kept) == 2
    assert kept[1]["raw_payload"]["provenance"]["source_class"] == "DERIVED"
    assert (
        kept[0]["raw_payload"]["provenance"]["source_group"]
        == kept[1]["raw_payload"]["provenance"]["source_group"]
    )


def test_add_evidence_dedupes_identical_row():
    db = SessionLocal()
    try:
        created = _create_case(db, "AAPL")
        first = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="news",
                title="Same article",
                excerpt="Identical body text.",
                source_url="https://example.com/same",
            ),
        )
        second = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="news",
                title="Same article",
                excerpt="Identical body text.",
                source_url="https://example.com/same",
            ),
        )
        assert first is not None and second is not None
        assert first.id == second.id
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        assert detail.evidence_count == 1
    finally:
        db.close()


def test_browser_mcp_retrieval_method_is_accepted():
    enriched = evidence_graph.enrich_item(
        _item(
            "ir_page",
            "IR page via browser",
            url="https://investor.nvidia.com/",
            retrieval_method="browser_mcp",
        )
    )
    assert enriched["retrieval_method"] == "browser_mcp"
    assert enriched["raw_payload"]["provenance"]["retrieval_method"] == "browser_mcp"


def test_filter_primary_and_secondary():
    items = [
        evidence_graph.enrich_item(_nvda_release()),
        evidence_graph.enrich_item(_revenue_news()),
        evidence_graph.enrich_item(_sector_price()),
    ]
    primary = evidence_graph.filter_evidence(items, source_class_name="PRIMARY")
    secondary = evidence_graph.filter_evidence(items, source_class_name="SECONDARY")
    assert len(primary) == 1
    assert len(secondary) == 1


# ---------------------------------------------------------------------------
# Claims and links
# ---------------------------------------------------------------------------


def test_claim_types_are_classified():
    assert evidence_graph.infer_claim_type("Revenue increased 12%.") == "FACT"
    assert evidence_graph.infer_claim_type("Growth remained strong.") == "INTERPRETATION"
    assert (
        evidence_graph.infer_claim_type(
            "Guidance concerns likely contributed to the decline."
        )
        == "CAUSAL_EXPLANATION"
    )
    assert evidence_graph.infer_claim_type("Export rules are a downside risk.") == "RISK"


def test_claim_creation_and_support_contradict_context_links():
    db = SessionLocal()
    try:
        created = _create_case(db, "MSFT")
        release = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="ir_page",
                title="NVIDIA Q2 FY2027 Earnings Release",
                excerpt="Gross margin guidance declined.",
                source_url="https://investor.nvidia.com/q2",
            ),
        )
        beat = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="news",
                title="Revenue beat",
                excerpt="Strong revenue result and beat estimates.",
                source_url="https://www.reuters.com/beat",
            ),
        )
        sector = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="price",
                title="SOX decline",
                excerpt="Semiconductor sector decline.",
                retrieval_method="system",
            ),
        )
        claim = evidence_ledger_store.add_claim(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=ClaimCreateRequest(
                statement="Guidance concerns likely contributed to the decline.",
                stance="supports_move",
                rank=1,
            ),
        )
        assert claim is not None
        assert claim.claim_type == "CAUSAL_EXPLANATION"
        evidence_ledger_store.link_claim_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            claim_id=claim.id,
            evidence_id=release.id,
            relation="supports",
        )
        evidence_ledger_store.link_claim_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            claim_id=claim.id,
            evidence_id=beat.id,
            relation="contradicts",
        )
        evidence_ledger_store.link_claim_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            claim_id=claim.id,
            evidence_id=sector.id,
            relation="context",
        )
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        row = detail.claims[0]
        relations = {link.relation for link in row.evidence_links}
        assert relations == {"supports", "contradicts", "context"}
        assert row.support_assessment["supporting_count"] >= 1
        assert row.support_assessment["contradicting_count"] >= 1
        assert row.support_assessment["context_count"] >= 1
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def test_temporal_pre_event_event_time_post_event_unknown():
    pre = evidence_graph.enrich_item(
        _item("ir_page", "Aug 19 release", published="2026-08-19"),
        event_time=EVENT_TIME,
    )
    same = evidence_graph.enrich_item(
        _item("ir_page", "Aug 20 announcement", published="2026-08-20"),
        event_time=EVENT_TIME,
    )
    post = evidence_graph.enrich_item(
        _item("news", "Aug 25 analyst note", published="2026-08-25"),
        event_time=EVENT_TIME,
    )
    unknown = evidence_graph.enrich_item(_item("news", "Undated blurb"))
    assert pre["raw_payload"]["provenance"]["temporal_relevance"] == "PRE_EVENT"
    assert same["raw_payload"]["provenance"]["temporal_relevance"] == "EVENT_TIME"
    assert post["raw_payload"]["provenance"]["temporal_relevance"] == "POST_EVENT"
    assert unknown["raw_payload"]["provenance"]["temporal_relevance"] == "UNKNOWN"


def test_post_event_support_is_labeled_and_gaps_causal_claim():
    post = evidence_graph.enrich_item(
        _item(
            "news",
            "Analyst says guidance caused the drop",
            "Guidance concerns likely contributed.",
            published="2026-08-25",
        ),
        event_time=EVENT_TIME,
    )
    assessment = evidence_graph.assess_claim(
        "Guidance concerns likely contributed to the decline.",
        [post],
        supporting_indices=[0],
    )
    assert any("post-event" in gap.lower() for gap in assessment.gaps)


# ---------------------------------------------------------------------------
# Source quality
# ---------------------------------------------------------------------------


def test_source_quality_primary_secondary_low():
    primary = _nvda_release()
    secondary = _revenue_news()
    low = _item("other", "Unverified social post", "someone tweeted")
    assert evidence_graph.source_class(primary) == "PRIMARY"
    assert evidence_graph.source_tier(primary) == "TIER_1"
    assert evidence_graph.source_class(secondary) == "SECONDARY"
    assert evidence_graph.source_tier(secondary) == "TIER_3"
    assert evidence_graph.source_tier(low) == "TIER_4"


def test_price_is_low_relevance_for_guidance_claim():
    price = _sector_price()
    assert (
        evidence_graph.relevance_for_claim(
            "NVDA guidance weakened.", price
        )
        == "LOW"
    )
    release = _nvda_release()
    assert (
        evidence_graph.relevance_for_claim(
            "NVDA guidance weakened.", release
        )
        == "HIGH"
    )


# ---------------------------------------------------------------------------
# Support assessment
# ---------------------------------------------------------------------------


def test_strongly_supported_guidance_example():
    items = [
        evidence_graph.enrich_item(_nvda_release(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_nvda_transcript(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_news(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_sector_price(), event_time=EVENT_TIME),
    ]
    assessment = evidence_graph.assess_claim(
        "Guidance concerns likely contributed to the decline.",
        items,
        supporting_indices=[0, 1],
        contradicting_indices=[2],
        context_indices=[3],
    )
    assert assessment.status == "STRONGLY_SUPPORTED"
    assert assessment.claim_type == "CAUSAL_EXPLANATION"
    assert assessment.primary_support >= 2
    assert assessment.independent_support >= 2
    assert assessment.contradicting_count == 1


def test_supported_fact_from_primary():
    items = [evidence_graph.enrich_item(_nvda_release())]
    assessment = evidence_graph.assess_claim(
        "Gross margin guidance declined.",
        items,
        supporting_indices=[0],
    )
    assert assessment.claim_type == "FACT"
    assert assessment.status == "SUPPORTED"


def test_partially_supported_with_news_only_causal():
    items = [evidence_graph.enrich_item(_revenue_news())]
    assessment = evidence_graph.assess_claim(
        "Guidance concerns likely contributed to the decline.",
        items,
        supporting_indices=[0],
    )
    assert assessment.status in {"PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE"}


def test_insufficient_evidence_missing_primary_regulatory():
    news = evidence_graph.enrich_item(
        _item(
            "news",
            "Blog says export rules hurt NVDA",
            "Regulatory restrictions may have caused the decline.",
            url="https://example.com/blog",
        )
    )
    assessment = evidence_graph.assess_claim(
        "Regulatory restrictions caused the decline.",
        [news],
        supporting_indices=[0],
    )
    assert assessment.status == "INSUFFICIENT_EVIDENCE"
    assert any("primary" in gap.lower() for gap in assessment.gaps)


def test_unsupported_claim_has_no_evidence():
    assessment = evidence_graph.assess_claim(
        "Growth remained strong.",
        [],
        supporting_indices=[],
    )
    assert assessment.status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"}
    assert assessment.supporting_count == 0


def test_contradicted_when_only_counterevidence():
    contra = evidence_graph.enrich_item(
        _item(
            "ir_page",
            "Guidance unchanged",
            "Annual guidance unchanged. Company raised outlook.",
            url="https://investor.nvidia.com/unchanged",
        )
    )
    assessment = evidence_graph.assess_claim(
        "Guidance concerns likely contributed to the decline.",
        [contra],
        supporting_indices=[],
        contradicting_indices=[0],
    )
    assert assessment.status == "CONTRADICTED"


def test_claim_without_evidence_cannot_be_strongly_supported():
    snap = evidence_graph.build_snapshot(
        [
            {
                "statement": "Guidance concerns likely contributed to the decline.",
                "supporting_indices": [],
                "evidence_indices": [],
                "rank": 1,
            }
        ],
        [],
    )
    assert snap.assessments[0]["status"] != "STRONGLY_SUPPORTED"
    assert snap.assessments[0]["status"] != "SUPPORTED"


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


def test_direct_contradiction():
    support = [
        _item("ir_page", "Cut", "Company cut guidance and lowered outlook.")
    ]
    contra = [
        _item("news", "Unchanged", "Annual guidance unchanged and raised outlook.")
    ]
    kind, note = evidence_graph.detect_conflict(support, contra)
    assert kind == "DIRECT_CONTRADICTION"
    assert note


def test_temporal_difference_quarter_vs_annual():
    support = [_item("ir_page", "Q2", "Next-quarter guidance was lowered.")]
    contra = [_item("news", "FY", "Annual guidance unchanged for the full year.")]
    kind, _ = evidence_graph.detect_conflict(support, contra)
    assert kind == "TEMPORAL_DIFFERENCE"


def test_contextual_difference():
    support = [_item("ir_page", "Margins", "Gross margin guidance declined.")]
    contra = [_item("news", "Revenue", "Strong revenue result and beat estimates.")]
    kind, _ = evidence_graph.detect_conflict(support, contra)
    assert kind == "CONTEXTUAL_DIFFERENCE"


def test_no_conflict_without_contra():
    kind, note = evidence_graph.detect_conflict([_nvda_release()], [])
    assert kind == "NO_CONFLICT"
    assert note == ""


# ---------------------------------------------------------------------------
# Evidence gaps
# ---------------------------------------------------------------------------


def test_evidence_gap_detected_in_snapshot():
    snap = evidence_graph.build_snapshot(
        [
            {
                "statement": "Regulatory restrictions caused the decline.",
                "supporting_indices": [0],
                "evidence_indices": [0],
            }
        ],
        [
            evidence_graph.enrich_item(
                _item("news", "Secondary rumor", "Export chatter in blogs.")
            )
        ],
    )
    assert snap.gaps
    assert snap.summary.insufficient >= 1 or snap.assessments[0]["status"] == (
        "INSUFFICIENT_EVIDENCE"
    )


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_fabricated_url_is_rejected():
    ok, reason = evidence_graph.validate_citations(
        "According to https://totally-fake.example/nvda the company collapsed.",
        [_nvda_release()],
        [0],
    )
    assert ok is False
    assert "URL" in reason or "citation" in reason.lower()
    cleaned = evidence_graph.strip_unknown_citations(
        "See https://totally-fake.example/nvda for proof.",
        [_nvda_release()],
    )
    assert "totally-fake" not in cleaned
    assert "citation removed" in cleaned


def test_according_to_without_link_is_rejected():
    ok, reason = evidence_graph.validate_citations(
        "According to NVIDIA, guidance collapsed.",
        [_nvda_release()],
        [],
    )
    assert ok is False
    assert "persisted" in reason.lower() or "reference" in reason.lower()


def test_invalid_evidence_index_is_rejected():
    ok, reason = evidence_graph.validate_citations(
        "Guidance declined.",
        [_nvda_release()],
        [9],
    )
    assert ok is False


def test_verification_rejects_fabricated_citation():
    from app.agents import investigation_verification_agent

    result = asyncio.get_event_loop().run_until_complete(
        investigation_verification_agent.verify_hypotheses(
            "NVDA",
            [
                {
                    "statement": "According to https://fake.example/x guidance collapsed.",
                    "stance": "supports_move",
                    "evidence_indices": [0],
                    "weight": 0.8,
                }
            ],
            [_nvda_release()],
        )
    )
    assert result["rejected"]
    assert all(
        "fake.example" not in (h.get("statement") or "")
        for h in result["hypotheses"]
    )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_investigation_evidence_summary_counts():
    items = [
        evidence_graph.enrich_item(_nvda_release()),
        evidence_graph.enrich_item(_nvda_transcript()),
        evidence_graph.enrich_item(_revenue_news()),
        evidence_graph.enrich_item(_sector_price()),
    ]
    assessments = [
        evidence_graph.assess_claim(
            "Guidance concerns likely contributed to the decline.",
            items,
            supporting_indices=[0, 1],
            contradicting_indices=[2],
            context_indices=[3],
        ),
        evidence_graph.assess_claim(
            "Revenue increased 12%.",
            items,
            supporting_indices=[2],
        ),
    ]
    summary = evidence_graph.summarize_investigation(items, assessments)
    assert summary.total_evidence == 4
    assert summary.primary == 2
    assert summary.secondary == 1
    assert summary.contextual == 1
    assert summary.claims == 2
    assert summary.strongly_supported >= 1
    dumped = summary.model_dump()
    assert "primary_source_coverage" in dumped
    assert "evidence_gaps" in dumped


# ---------------------------------------------------------------------------
# Persistence of claims + summary
# ---------------------------------------------------------------------------


def test_replace_system_rows_exposes_summary_and_support_status():
    db = SessionLocal()
    try:
        created = _create_case(db, "AMD")
        items = [
            evidence_graph.enrich_item(_nvda_release(), event_time=EVENT_TIME),
            evidence_graph.enrich_item(_nvda_transcript(), event_time=EVENT_TIME),
            evidence_graph.enrich_item(_revenue_news(), event_time=EVENT_TIME),
        ]
        ids = evidence_ledger_store.replace_system_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            items=items,
        )
        assert len(ids) == 3
        evidence_ledger_store.replace_system_claims(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            hypotheses=[
                {
                    "statement": "Guidance concerns likely contributed to the decline.",
                    "stance": "supports_move",
                    "weight": 0.6,
                    "rank": 1,
                    "supporting_indices": [0, 1],
                    "contradicting_indices": [2],
                    "neutral_indices": [],
                }
            ],
            evidence_ids=ids,
        )
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        assert detail.evidence_summary["total_evidence"] == 3
        assert detail.claims[0].support_status
        assert detail.evidence_items[0].temporal_relevance in {
            "PRE_EVENT",
            "EVENT_TIME",
            "POST_EVENT",
            "UNKNOWN",
        }
        assert detail.evidence_items[0].temporal_relevance == "PRE_EVENT"
    finally:
        db.close()


def test_replace_strips_fabricated_url_from_persisted_claim():
    db = SessionLocal()
    try:
        created = _create_case(db, "INTC")
        ids = evidence_ledger_store.replace_system_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            items=[_nvda_release()],
        )
        evidence_ledger_store.replace_system_claims(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            hypotheses=[
                {
                    "statement": (
                        "According to https://hallucinated.example/filing "
                        "guidance collapsed."
                    ),
                    "stance": "supports_move",
                    "weight": 0.5,
                    "rank": 1,
                    "evidence_indices": [0],
                }
            ],
            evidence_ids=ids,
        )
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        assert "hallucinated.example" not in detail.claims[0].statement
        assert detail.claims[0].support_status == "UNSUPPORTED"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Verification / Devil's Advocate integration
# ---------------------------------------------------------------------------


def test_verification_consumes_claim_assessments():
    from app.agents import investigation_verification_agent

    ctx = {
        "claim_assessments": [
            {
                "status": "INSUFFICIENT_EVIDENCE",
                "claim_type": "CAUSAL_EXPLANATION",
                "conflict": "NO_CONFLICT",
            }
        ]
    }
    result = asyncio.get_event_loop().run_until_complete(
        investigation_verification_agent.verify_hypotheses(
            "NVDA",
            [
                {
                    "statement": "Guidance concerns likely contributed to the decline.",
                    "stance": "supports_move",
                    "evidence_indices": [0],
                    "weight": 0.7,
                }
            ],
            [_nvda_release()],
            hypothesis_context=ctx,
        )
    )
    assert any("INSUFFICIENT_EVIDENCE" in n for n in result["notes"])


def test_devils_advocate_consumes_evidence_conflicts_and_gaps():
    from app.agents import devils_advocate_agent

    captured = {}

    async def fake_llm(**kwargs):
        captured["prompt"] = kwargs.get("user_prompt") or ""
        return {
            "counterargument": "Revenue beat undercuts a pure guidance story.",
            "materially_weakened": False,
            "confidence_delta": 0.0,
            "promote_competitor_index": None,
            "notes": [],
            "overweighted_evidence": "Secondary blogs",
            "ignored_evidence": "Revenue beat",
            "alternative_more_plausible": False,
            "additional_evidence_needed": "Transcript",
        }

    snapshot = evidence_graph.build_snapshot(
        [
            {
                "statement": "Guidance concerns likely contributed to the decline.",
                "supporting_indices": [0],
                "contradicting_indices": [1],
                "evidence_indices": [0, 1],
                "rank": 1,
            }
        ],
        [
            evidence_graph.enrich_item(_nvda_release()),
            evidence_graph.enrich_item(_revenue_news()),
        ],
    )
    ctx = evidence_graph.advocate_context(snapshot)
    ctx["claim_assessments"] = snapshot.assessments

    async def _run():
        with patch(
            "app.agents.devils_advocate_agent.call_openai_json",
            new=fake_llm,
        ):
            return await devils_advocate_agent.stress_test_leading(
                _move(),
                [
                    {
                        "statement": "Guidance concerns likely contributed to the decline.",
                        "stance": "supports_move",
                        "weight": 0.6,
                        "evidence_indices": [0],
                    },
                    {
                        "statement": "Sector decline",
                        "stance": "market_noise",
                        "weight": 0.4,
                        "evidence_indices": [1],
                    },
                ],
                [_nvda_release(), _revenue_news()],
                hypothesis_context=ctx,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert "hypothesis_context" in captured["prompt"]
    assert "evidence_gaps" in captured["prompt"] or "cherry_pick" in captured["prompt"]
    assert result["hypothesis_context"]["evidence_conflicts"] or result[
        "hypothesis_context"
    ].get("evidence_gaps") is not None


# ---------------------------------------------------------------------------
# Phase 3 / 4 still work
# ---------------------------------------------------------------------------


def test_phase3_graph_still_runs_and_persists():
    ranked = {
        "summary": "Guidance is the leading support score.",
        "hypotheses": [
            {
                "statement": "Guidance concerns likely contributed to the decline.",
                "stance": "supports_move",
                "confidence_score": 0.7,
                "weight": 0.55,
                "devil_advocate_notes": "",
                "evidence_indices": [1],
                "supporting_indices": [1],
                "rank": 1,
            },
            {
                "statement": "Moved with the sector",
                "stance": "market_noise",
                "confidence_score": 0.4,
                "weight": 0.45,
                "evidence_indices": [0],
                "rank": 2,
            },
        ],
    }
    evidence = [
        _item("price", "NVDA 1d move", "down 8.7%", retrieval_method="system"),
        _nvda_release(),
    ]
    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(return_value=_move()),
    ), patch(
        "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
        new=AsyncMock(
            return_value={
                "focus": "Why NVDA fell",
                "tools": ["get_stock_news"],
                "seed_hypotheses": ["Guidance concerns", "Macro"],
                "skip_reason": None,
            }
        ),
    ), patch(
        "app.services.investigation_runner._collect_evidence",
        new=AsyncMock(return_value=evidence),
    ), patch(
        "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
        new=AsyncMock(return_value=ranked),
    ), patch(
        "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
        new=AsyncMock(
            return_value={
                "hypotheses": ranked["hypotheses"],
                "rejected": [],
                "notes": [],
                "citation_coverage": 1.0,
            }
        ),
    ), patch(
        "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
        new=AsyncMock(
            return_value={
                "hypotheses": ranked["hypotheses"],
                "outcome": "held",
                "counterargument": "Sector beta remains plausible.",
                "leading_weakened": False,
                "reversal": False,
                "notes": [],
                "confidence_delta": 0.0,
            }
        ),
    ):
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={"ticker": "nvda", "window_label": "1d"},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "complete"
    assert body["claims_count"] == 2
    assert body["evidence_count"] == 2
    assert body["evidence_summary"]["total_evidence"] == 2
    assert body["roster"]["evidence_graph"]["summary"]["total_evidence"] == 2
    assert body["claims"][0]["support_status"]
    assert "export" in body["summary"].lower() or "guidance" in body["summary"].lower()


def test_phase4_ranking_still_returns_relative_support():
    from app.agents import hypothesis_ranker_agent
    from app.models.hypothesis_schemas import HypothesisRecord

    plan = {
        "focus": "NVDA",
        "seed_hypotheses": ["Guidance concerns", "Macro selloff"],
        "hypothesis_records": [
            HypothesisRecord(
                hypothesis_id="H1",
                title="Guidance concerns",
                category="GUIDANCE",
            ).model_dump(),
            HypothesisRecord(
                hypothesis_id="H2",
                title="Macro selloff",
                category="MARKET_WIDE",
            ).model_dump(),
        ],
    }

    async def _run():
        with patch(
            "app.agents.hypothesis_ranker_agent.call_openai_json",
            new=AsyncMock(
                return_value={
                    "summary": "Guidance leads.",
                    "hypotheses": [
                        {
                            "statement": "Guidance concerns",
                            "stance": "supports_move",
                            "confidence_score": 0.9,
                            "weight": 0.9,
                            "evidence_indices": [0],
                        }
                    ],
                }
            ),
        ):
            return await hypothesis_ranker_agent.rank_hypotheses(
                _move(),
                plan,
                [_nvda_release()],
            )

    ranked = asyncio.get_event_loop().run_until_complete(_run())
    assert ranked["hypotheses"][0].get("confidence_level")
    assert "engine" in ranked


def test_existing_list_api_shape_is_additive():
    created = client.post(
        "/api/investigations",
        headers=GUEST,
        json={"ticker": "CRM", "trigger_reason": "on_demand"},
    )
    assert created.status_code == 201, created.text
    listed = client.get("/api/investigations", headers=GUEST)
    assert listed.status_code == 200
    row = listed.json()[0]
    assert {"id", "ticker", "status", "claims_count", "evidence_count"} <= set(row)
    detail = client.get(f"/api/investigations/{created.json()['id']}", headers=GUEST)
    assert detail.status_code == 200
    body = detail.json()
    assert "evidence_summary" in body
    assert "claims" in body
    assert "evidence_items" in body


def test_link_api_still_accepts_supports_contradicts_context():
    created = client.post(
        "/api/investigations",
        headers=GUEST,
        json={"ticker": "ORCL", "trigger_reason": "on_demand"},
    )
    inv_id = created.json()["id"]
    ev = client.post(
        f"/api/investigations/{inv_id}/evidence",
        headers=GUEST,
        json={
            "source_type": "ir_page",
            "title": "IR",
            "excerpt": "Guidance declined.",
            "source_url": "https://investor.example.com/a",
        },
    )
    claim = client.post(
        f"/api/investigations/{inv_id}/claims",
        headers=GUEST,
        json={
            "statement": "Guidance concerns likely contributed to the decline.",
            "stance": "supports_move",
            "rank": 1,
        },
    )
    link = client.post(
        f"/api/investigations/{inv_id}/links",
        headers=GUEST,
        json={
            "claim_id": claim.json()["id"],
            "evidence_id": ev.json()["id"],
            "relation": "supports",
        },
    )
    assert link.status_code == 201, link.text
    assert link.json()["relation"] == "supports"
    assert link.json()["evidence"]["content_hash"]

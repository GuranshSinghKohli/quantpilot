"""Phase 7 — verification layers and Devil's Advocate hardening."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase7.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_GRAPH_ENABLED"] = "true"
os.environ["MAX_INVESTIGATION_ITERATIONS"] = "2"
os.environ["MAX_CHALLENGE_LOOPS"] = "1"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import devils_advocate_agent, investigation_verification_agent  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    DevilsAdvocateOutcome,
    InvestigationCreateRequest,
)
from app.services import evidence_graph, evidence_ledger_store, verification_engine  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.workflows import investigation_graph  # noqa: E402

init_db()
client = TestClient(app)
OWNER = "guest:phase7guestaaaaaaaaaaaaaaaaaaaa"
GUEST = {"X-Guest-Id": "phase7guestaaaaaaaaaaaaaaaaaaaa"}
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


def _price() -> dict:
    return _item(
        "price",
        "NVDA 1d move",
        "NVDA fell 8.7%",
        retrieval_method="system",
    )


def _ir_guidance() -> dict:
    return _item(
        "ir_page",
        "NVIDIA Q2 FY2027 Earnings Release",
        "Gross margin guidance declined. Next-quarter outlook lowered.",
        url="https://investor.nvidia.com/q2-fy2027",
        published="2026-08-19",
        extra={"retrieved_at": "2026-08-20T18:00:00+00:00", "publisher": "NVIDIA IR"},
    )


def _revenue_beat() -> dict:
    return _item(
        "news",
        "NVIDIA revenue beat estimates",
        "Revenue increased 12% and beat estimates. Strong revenue result.",
        url="https://www.reuters.com/nvda-beat",
        published="2026-08-20",
        extra={"retrieved_at": "2026-08-20T18:05:00+00:00"},
    )


def _annual_unchanged() -> dict:
    return _item(
        "news",
        "Annual guidance unchanged",
        "Annual guidance unchanged despite the quarter cut.",
        url="https://www.cnbc.com/nvda-annual",
        published="2026-08-20",
        extra={"retrieved_at": "2026-08-20T18:06:00+00:00"},
    )


def _sector() -> dict:
    return _item(
        "price",
        "Semiconductor sector decline",
        "Semiconductor sector declined with NVDA. Broad semiconductor weakness.",
        published="2026-08-20",
        retrieval_method="system",
    )


def _post_event_recap() -> dict:
    return _item(
        "news",
        "Weekend recap: guidance caused the selloff",
        "Commentators said guidance caused last week's decline.",
        url="https://www.marketwatch.com/nvda-recap",
        published="2026-08-25",
        extra={"retrieved_at": "2026-08-25T12:00:00+00:00"},
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


def _plan() -> dict:
    return {
        "focus": "Why NVDA fell 8.7%",
        "tools": ["get_stock_news"],
        "seed_hypotheses": ["Guidance concerns", "Macro selloff"],
        "skip_reason": None,
        "need_browser_ir": False,
    }


def _ranked(statement: str = "Guidance concerns caused the decline.") -> dict:
    return {
        "summary": "Guidance is the leading support score.",
        "hypotheses": [
            {
                "statement": statement,
                "stance": "supports_move",
                "confidence_score": 0.7,
                "weight": 0.55,
                "devil_advocate_notes": "",
                "evidence_indices": [1],
                "supporting_indices": [1],
                "contradicting_indices": [2],
                "rank": 1,
            },
            {
                "statement": "Broader semiconductor weakness also contributed.",
                "stance": "market_noise",
                "confidence_score": 0.4,
                "weight": 0.45,
                "devil_advocate_notes": "",
                "evidence_indices": [0],
                "rank": 2,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Level 1 — source
# ---------------------------------------------------------------------------


def test_source_level_flags_empty_and_invalid_url():
    empty = verification_engine.check_source(
        0, {"source_type": "news", "title": "", "excerpt": "", "source_url": ""}
    )
    assert empty.ok is False
    assert any("empty" in i.lower() for i in empty.issues)
    assert any("url" in i.lower() for i in empty.issues)

    price = verification_engine.check_source(1, _price())
    assert price.ok is True

    bad_url = verification_engine.check_source(
        2,
        _item("news", "Headline", url="not-a-url", published="2026-08-20"),
    )
    assert bad_url.ok is False
    assert any("valid" in i.lower() or "url" in i.lower() for i in bad_url.issues)


def test_source_level_official_domain_and_dates():
    ir = verification_engine.check_source(
        0,
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        event_time=EVENT_TIME,
    )
    assert ir.source_type == "ir_page"
    assert ir.temporal_relevance == "PRE_EVENT"

    blog_ir = verification_engine.check_source(
        1,
        _item(
            "ir_page",
            "Fake IR",
            "guidance",
            url="https://random-blog.example/nvda",
            published="2026-08-19",
            extra={"retrieved_at": "2026-08-20T18:00:00+00:00"},
        ),
        event_time=EVENT_TIME,
    )
    assert blog_ir.ok is False
    assert any("official" in i.lower() for i in blog_ir.issues)


# ---------------------------------------------------------------------------
# Level 2 / 3 / 4 + claim types
# ---------------------------------------------------------------------------


def test_fact_is_not_treated_as_causal_proof():
    items = [
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
    ]
    hyps = [
        {
            "statement": "Revenue increased 12%.",
            "supporting_indices": [1],
            "evidence_indices": [1],
            "rank": 2,
        },
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "contradicting_indices": [1],
            "evidence_indices": [0, 1],
            "rank": 1,
        },
    ]
    rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    fact = next(c for c in report.claim_checks if "Revenue increased" in c.statement)
    causal = next(c for c in report.claim_checks if "Guidance" in c.statement or "strongest supported" in c.statement)
    assert fact.claim_type == "FACT"
    assert causal.claim_type == "CAUSAL_EXPLANATION"
    assert rewritten[1]["statement"] != "Guidance concerns caused the decline."
    assert "strongest supported explanation" in rewritten[1]["statement"].lower()
    assert any("beat" in (n or "").lower() or "contradict" in (n or "").lower() for n in report.notes)


def test_interpretation_is_distinct_from_fact():
    items = [evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME)]
    hyps = [
        {
            "statement": "Investors may have viewed the margin outlook negatively.",
            "supporting_indices": [0],
            "evidence_indices": [0],
            "rank": 1,
        }
    ]
    rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.claim_checks[0].claim_type == "INTERPRETATION"
    assert rewritten[0]["statement"] == hyps[0]["statement"]


def test_causal_guardrail_prefers_hedged_form():
    text, hedged = verification_engine.apply_causal_guardrail(
        "Guidance concerns caused the decline.",
        "CAUSAL_EXPLANATION",
        "PARTIALLY_SUPPORTED",
        alternative="Broader semiconductor weakness also contributed.",
    )
    assert hedged is True
    assert "strongest supported explanation" in text.lower()
    assert "semiconductor" in text.lower()
    fact, fact_hedged = verification_engine.apply_causal_guardrail(
        "Revenue increased 12%.", "FACT", "SUPPORTED"
    )
    assert fact_hedged is False
    assert fact == "Revenue increased 12%."


def test_hypothesis_layer_requires_alternative_and_surfaces_gaps():
    items = [evidence_graph.enrich_item(_price(), event_time=EVENT_TIME)]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "evidence_indices": [0],
            "supporting_indices": [0],
            "rank": 1,
        }
    ]
    _rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.hypothesis_check.alternative_considered is False
    assert report.hypothesis_check.gaps or report.hypothesis_check.issues


def test_conclusion_layer_records_uncertainty_when_causal_is_weak():
    items = [
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_sector(), event_time=EVENT_TIME),
    ]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "contradicting_indices": [1],
            "evidence_indices": [0, 1, 2],
            "rank": 1,
        },
        {
            "statement": "Broader semiconductor weakness also contributed.",
            "supporting_indices": [2],
            "evidence_indices": [2],
            "rank": 2,
        },
    ]
    rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.conclusion_check.uncertainty_represented is True
    assert report.conclusion_check.hedged_statements
    assert "strongest supported" in rewritten[0]["statement"].lower()


# ---------------------------------------------------------------------------
# Temporal + independence + contradictions
# ---------------------------------------------------------------------------


def test_post_event_evidence_is_retrospective():
    items = [
        evidence_graph.enrich_item(_post_event_recap(), event_time=EVENT_TIME),
    ]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "evidence_indices": [0],
            "rank": 1,
        }
    ]
    _rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.source_checks[0].temporal_relevance == "POST_EVENT"
    assert report.claim_checks[0].retrospective is True
    assert any("retrospective" in n.lower() or "post-event" in n.lower() for n in report.notes)


def test_pre_event_and_event_time_are_valid():
    items = [
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
    ]
    assert verification_engine.check_source(0, items[0], event_time=EVENT_TIME).temporal_relevance == "PRE_EVENT"
    assert verification_engine.check_source(1, items[1], event_time=EVENT_TIME).temporal_relevance == "EVENT_TIME"


def test_source_group_does_not_double_count_reprints():
    shared = "NVIDIA reported that gross margin guidance declined."
    reprints = [
        _item("news", shared, shared, url="https://www.reuters.com/a", published="2026-08-20"),
        _item("news", shared, shared, url="https://www.cnbc.com/a", published="2026-08-20"),
        _item("news", shared, shared, url="https://www.marketwatch.com/a", published="2026-08-20"),
        _item("news", shared, shared, url="https://finance.yahoo.com/a", published="2026-08-20"),
    ]
    items, dropped = evidence_graph.dedupe_items(reprints, event_time=EVENT_TIME)
    assert dropped == 0
    groups = {item.get("source_group") for item in items}
    assert len(groups) == 1
    hyps = [
        {
            "statement": "Gross margin guidance declined.",
            "supporting_indices": list(range(len(items))),
            "evidence_indices": list(range(len(items))),
            "rank": 1,
        }
    ]
    _rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.claim_checks[0].independent_support == 1


def test_contradictions_are_surfaced_not_hidden():
    items = [
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_annual_unchanged(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
    ]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "contradicting_indices": [1],
            "evidence_indices": [0, 1, 2],
            "rank": 1,
        }
    ]
    _rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    blob = " ".join(report.notes + report.claim_checks[0].contradictions).lower()
    assert "unchanged" in blob or "beat" in blob or "contradict" in blob
    weakeners = verification_engine.find_weakeners(
        "Guidance concerns caused the decline.", items
    )
    assert weakeners


# ---------------------------------------------------------------------------
# Verification agent + Devil's Advocate
# ---------------------------------------------------------------------------


def test_verification_agent_attaches_report_and_keeps_fr3():
    evidence = [_price(), _ir_guidance()]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "stance": "supports_move",
            "weight": 0.7,
            "confidence_score": 0.7,
            "evidence_indices": [1],
            "rank": 1,
        },
        {
            "statement": "Secret product delay nobody reported",
            "stance": "supports_move",
            "weight": 0.3,
            "confidence_score": 0.3,
            "evidence_indices": [],
            "rank": 2,
        },
    ]
    result = asyncio.get_event_loop().run_until_complete(
        investigation_verification_agent.verify_hypotheses(
            "NVDA", hyps, evidence, event_time=EVENT_TIME
        )
    )
    assert result["verification_report"]
    assert all(h.get("evidence_indices") for h in result["hypotheses"])
    assert any("Secret product" in (r.get("statement") or "") for r in result["rejected"])


def test_devils_advocate_structured_output_and_severity():
    hyps = [
        {
            "statement": "Company-specific scare caused the print.",
            "stance": "supports_move",
            "weight": 0.7,
            "confidence_score": 0.7,
            "evidence_indices": [0],
            "hypothesis_id": "H1",
            "rank": 1,
        },
        {
            "statement": "Sector beta",
            "stance": "market_noise",
            "weight": 0.3,
            "confidence_score": 0.3,
            "evidence_indices": [0],
            "hypothesis_id": "H2",
            "rank": 2,
        },
    ]
    result = asyncio.get_event_loop().run_until_complete(
        devils_advocate_agent.stress_test_leading(_move(), hyps, [_price()])
    )
    assert result["challenge_severity"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert result["recommendation"] in {"ACCEPT", "REVIEW", "RESEARCH_MORE", "REJECT"}
    assert result["strongest_counterargument"]
    assert isinstance(result["additional_evidence_needed"], list)
    assert isinstance(result["questionable_assumptions"], list)
    assert result["devils_advocate_result"]["target_hypothesis_id"]
    assert result["leading_weakened"] is True
    assert result["challenge_severity"] in {"HIGH", "CRITICAL"}


def test_challenge_classifier_critical_vs_low():
    sev, rec = verification_engine.classify_challenge(
        status="UNSUPPORTED",
        outcome="held",
        alternative_more_plausible=False,
        contradictions=False,
        cherry_pick=False,
        temporal_leakage=False,
        leading_supported=False,
        has_primary=False,
        claim_type="CAUSAL_EXPLANATION",
        additional_needed=["filing"],
    )
    assert sev == "CRITICAL"
    assert rec == "RESEARCH_MORE"
    sev2, rec2 = verification_engine.classify_challenge(
        status="SUPPORTED",
        outcome="held",
        alternative_more_plausible=False,
        contradictions=False,
        cherry_pick=False,
        temporal_leakage=False,
        leading_supported=True,
        has_primary=True,
        claim_type="FACT",
        additional_needed=[],
    )
    assert sev2 == "LOW"
    assert rec2 == "ACCEPT"


# ---------------------------------------------------------------------------
# LangGraph loop
# ---------------------------------------------------------------------------


def test_graph_accept_does_not_request_more_research():
    investigation_graph.reset_investigation_graph()
    ranked = _ranked()
    evidence = [_price(), _ir_guidance(), _revenue_beat(), _item("filing", "8-K", "Item 2.02", url="https://www.sec.gov/a", published="2026-08-20")]
    collect = AsyncMock(return_value=evidence)

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
        ), patch(
            "app.services.investigation_runner._collect_evidence",
            new=collect,
        ), patch(
            "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
            new=AsyncMock(return_value=ranked),
        ), patch(
            "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "rejected": [],
                    "notes": ["grounded"],
                    "citation_coverage": 1.0,
                    "verification_report": {},
                }
            ),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "outcome": "held",
                    "counterargument": "Sector tape remains plausible.",
                    "leading_weakened": False,
                    "reversal": False,
                    "notes": [],
                    "confidence_delta": 0.0,
                }
            ),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=71,
                event={"occurred_at": "2026-08-20T16:00:00+00:00"},
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert collect.await_count == 1
    assert result.challenge_loops == 0
    assert result.status == "complete"
    assert result.da.get("counterargument")


def test_graph_high_challenge_can_request_more_research():
    investigation_graph.reset_investigation_graph()
    ranked = _ranked()
    first = [_price(), _item("news", "Guidance cut chatter", published="2026-08-20"), _item("news", "Second headline", published="2026-08-20"), _item("filing", "8-K", "Item 2.02", url="https://www.sec.gov/a", published="2026-08-20")]
    second = first + [_ir_guidance()]
    collect = AsyncMock(side_effect=[first, second])
    da_first = {
        "hypotheses": ranked["hypotheses"],
        "outcome": "confidence_cut",
        "counterargument": "Need the official transcript before treating guidance as the cause.",
        "leading_weakened": True,
        "reversal": False,
        "notes": [],
        "confidence_delta": -0.1,
        "challenge_severity": "HIGH",
        "recommendation": "RESEARCH_MORE",
        "additional_evidence_needed": ["earnings transcript from IR"],
        "devils_advocate_result": {
            "target_hypothesis_id": "H1",
            "strongest_counterargument": "Need the official transcript.",
            "contradictory_evidence": ["Revenue beat"],
            "ignored_evidence": ["Sector decline"],
            "questionable_assumptions": ["Fact treated as cause"],
            "alternative_hypothesis_id": "H2",
            "alternative_strength": "plausible",
            "additional_evidence_needed": ["earnings transcript from IR"],
            "challenge_severity": "HIGH",
            "recommendation": "RESEARCH_MORE",
        },
    }
    da_second = {
        "hypotheses": ranked["hypotheses"],
        "outcome": "held",
        "counterargument": "Lead survives the extra pass.",
        "leading_weakened": False,
        "reversal": False,
        "notes": [],
        "confidence_delta": 0.0,
        "challenge_severity": "LOW",
        "recommendation": "ACCEPT",
        "additional_evidence_needed": [],
        "devils_advocate_result": {
            "strongest_counterargument": "Lead survives the extra pass.",
            "additional_evidence_needed": [],
            "challenge_severity": "LOW",
            "recommendation": "ACCEPT",
        },
    }

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
        ), patch(
            "app.services.investigation_runner._collect_evidence",
            new=collect,
        ), patch(
            "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
            new=AsyncMock(return_value=ranked),
        ), patch(
            "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "rejected": [],
                    "notes": ["grounded"],
                    "citation_coverage": 1.0,
                }
            ),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(side_effect=[da_first, da_second]),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=72,
                event={"occurred_at": "2026-08-20T16:00:00+00:00"},
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert collect.await_count == 2
    assert result.challenge_loops == 1
    assert result.status == "complete"
    second_plan = collect.await_args_list[1].args[1]
    assert "get_ir_materials" in (second_plan.get("tools") or [])


def test_phase3_mocked_held_da_still_completes():
    """Existing Phase 3 mocks omit recommendation and must not loop."""
    investigation_graph.reset_investigation_graph()
    ranked = _ranked()
    evidence = [_price(), _item("news", "A", published="2026-08-20"), _item("news", "B", published="2026-08-20"), _item("filing", "8-K", url="https://www.sec.gov/x", published="2026-08-20")]

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
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
                    "notes": ["All claims grounded."],
                    "citation_coverage": 1.0,
                }
            ),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "outcome": "held",
                    "counterargument": "A broad semiconductor drawdown remains plausible.",
                    "leading_weakened": False,
                    "reversal": False,
                    "notes": ["Alternative: macro/sector beta."],
                    "confidence_delta": 0.0,
                }
            ),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(), ticker="NVDA", investigation_id=73, max_iterations=2
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.status == "complete"
    assert result.challenge_loops == 0
    assert result.hypotheses[0]["evidence_indices"]


# ---------------------------------------------------------------------------
# API compatibility
# ---------------------------------------------------------------------------


def test_existing_investigation_api_accepts_additive_da_fields():
    db = SessionLocal()
    try:
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key=OWNER,
            user=None,
            body=InvestigationCreateRequest(
                ticker="NVDA",
                trigger_reason="on_demand",
                summary="Investigating NVDA.",
            ),
        )
        evidence_ledger_store.set_verification_audit(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            verification_notes="Causal claim hedged.",
            da_outcome={
                "outcome": "held",
                "counterargument": "Sector weakness remains plausible.",
                "leading_weakened": False,
                "reversal": False,
                "notes": [],
                "confidence_delta": 0.0,
                "citation_coverage": 1.0,
                "challenge_severity": "MEDIUM",
                "recommendation": "REVIEW",
                "alternative_hypothesis_id": "H2",
                "additional_evidence_needed": ["transcript"],
                "questionable_assumptions": ["Fact treated as cause"],
            },
        )
        detail = evidence_ledger_store.get_investigation(
            db, owner_key=OWNER, investigation_id=created.id
        )
        assert detail is not None
        assert detail.da_outcome is not None
        parsed = DevilsAdvocateOutcome.model_validate(detail.da_outcome.model_dump())
        assert parsed.outcome == "held"
        assert parsed.challenge_severity == "MEDIUM"
        assert parsed.recommendation == "REVIEW"
        assert parsed.additional_evidence_needed == ["transcript"]
    finally:
        db.close()

    listed = client.get("/api/investigations", headers=GUEST)
    assert listed.status_code == 200


def test_summary_mentions_opposing_case():
    from app.services.investigation_runner import _finalize_summary

    text = _finalize_summary(
        _move(),
        "Investigation complete.",
        {
            "outcome": "held",
            "counterargument": "Revenue beat and sector weakness remain plausible.",
            "challenge_severity": "MEDIUM",
            "recommendation": "REVIEW",
        },
        [
            {
                "statement": (
                    "Guidance concerns is the strongest supported explanation "
                    "identified by the investigation, although broader semiconductor "
                    "weakness also remains plausible."
                ),
                "contradicting_indices": [1],
            },
            {"statement": "Broader semiconductor weakness also contributed."},
        ],
    )
    assert "8.7" in text
    assert "strongest supported" in text.lower()
    assert "REVIEW" in text
    assert "Revenue beat" in text
    assert "%" not in text.split("8.7")[1] or "87.43" not in text


# ---------------------------------------------------------------------------
# Conclusion strength, gaps, hallucination, research-more reasons
# ---------------------------------------------------------------------------


def test_insufficient_evidence_is_a_valid_conclusion():
    items = [_price()]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "evidence_indices": [0],
            "rank": 1,
        }
    ]
    rewritten, report = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    assert report.conclusion_strength == "INSUFFICIENT_EVIDENCE"
    assert "Insufficient evidence to determine the primary cause" in rewritten[0]["statement"]
    assert report.evidence_gaps.gaps


def test_strong_causal_uses_contributor_language():
    text, hedged = verification_engine.apply_causal_guardrail(
        "Guidance concerns caused the decline.",
        "CAUSAL_EXPLANATION",
        "STRONGLY_SUPPORTED",
    )
    assert hedged is True
    assert "significant contributor" in text.lower()
    assert "because" not in text.lower()


def test_research_more_reasons_cover_required_conditions():
    lead = {
        "statement": "Guidance concerns caused the decline.",
        "claim_type": "CAUSAL_EXPLANATION",
        "support_status": "INSUFFICIENT_EVIDENCE",
        "weight": 0.48,
        "evidence_indices": [0],
        "supporting_indices": [0],
    }
    alt = {"statement": "Sector selloff", "weight": 0.46, "evidence_indices": [0]}
    check = verification_engine.ClaimEvidenceCheck(
        statement=lead["statement"],
        claim_type="CAUSAL_EXPLANATION",
        support_status="INSUFFICIENT_EVIDENCE",
        contradictions=["Sources disagree on 'cut guidance' vs 'guidance unchanged'."],
        retrospective=True,
    )
    reasons = verification_engine.research_more_reasons(
        lead, [_price()], check, alternative=alt
    )
    joined = " ".join(reasons)
    assert "primary" in joined
    assert "insufficient" in joined
    assert "causal" in joined
    assert "contradiction" in joined
    assert "alternative" in joined
    assert "temporal" in joined


def test_hallucinated_management_quote_is_rejected():
    kept, rejected = verification_engine.finalize_claims(
        [
            {
                "statement": "Management said demand was weakening across all segments.",
                "evidence_indices": [0],
                "hypothesis_id": "H9",
            }
        ],
        [_ir_guidance()],
        investigation_id=7,
    )
    assert any("demand was weakening" in s for s in rejected)
    assert kept[0]["statement"] == "Insufficient evidence to determine the primary cause."


def test_fabricated_citation_is_rejected():
    kept, rejected = verification_engine.finalize_claims(
        [
            {
                "statement": "According to https://hallucinated.example/filing guidance collapsed.",
                "evidence_indices": [0],
            }
        ],
        [_ir_guidance()],
    )
    assert rejected
    assert all("hallucinated.example" not in (h.get("statement") or "") for h in kept)


def test_invalid_evidence_index_is_rejected():
    kept, rejected = verification_engine.finalize_claims(
        [{"statement": "Revenue increased 12%.", "evidence_indices": [99]}],
        [_revenue_beat()],
    )
    assert rejected or kept[0]["statement"] == "Insufficient evidence to determine the primary cause."


def test_supported_fact_survives_final_pass():
    kept, rejected = verification_engine.finalize_claims(
        [
            {
                "statement": "Revenue increased 12%.",
                "evidence_indices": [0],
                "support_status": "SUPPORTED",
                "claim_type": "FACT",
            }
        ],
        [_revenue_beat()],
    )
    assert not rejected
    assert kept[0]["statement"] == "Revenue increased 12%."


def test_evidence_gap_report_lists_missing_primary_and_commentary():
    report = verification_engine.build_evidence_gap_report(
        [_item("news", "Only a headline", url="https://www.reuters.com/x", published="2026-08-20")]
    )
    blob = " ".join(report.gaps).lower()
    assert "management commentary" in blob
    assert "regulatory" in blob
    assert "secondary" in blob
    assert report.only_secondary_news is True


def test_final_report_has_required_sections_and_no_trade_advice():
    items = [
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_sector(), event_time=EVENT_TIME),
    ]
    hyps = [
        {
            "statement": "Guidance concerns caused the decline.",
            "supporting_indices": [0],
            "contradicting_indices": [1],
            "evidence_indices": [0, 1, 2],
            "rank": 1,
        },
        {
            "statement": "Broader semiconductor weakness also contributed.",
            "supporting_indices": [2],
            "evidence_indices": [2],
            "rank": 2,
        },
    ]
    rewritten, vrep = verification_engine.verify_investigation(
        hyps, items, event_time=EVENT_TIME
    )
    final = verification_engine.build_final_report(
        move_line="NVDA fell 8.7% over 1 day.",
        hypotheses=rewritten,
        evidence=items,
        verification=vrep,
        da={
            "challenge_severity": "MEDIUM",
            "recommendation": "REVIEW",
            "counterargument": "Revenue beat undercuts a pure guidance story.",
            "contradictory_evidence": ["NVIDIA revenue beat estimates"],
        },
        portfolio_exposure=0.05,
    )
    dumped = final.model_dump()
    for key in (
        "what_happened",
        "why_it_likely_happened",
        "strongest_alternative",
        "supporting_evidence",
        "contradicting_evidence",
        "verification",
        "devils_advocate",
        "evidence_gaps",
        "portfolio_impact",
        "final_assessment",
        "sources",
    ):
        assert key in dumped
    assert "8.7" in final.what_happened
    assert final.contradicting_evidence
    assert final.evidence_gaps
    assert final.trading_advice == ""
    assert "buy" not in final.final_assessment.lower()
    assert "87.43" not in final.final_assessment
    assert final.verification.claims_reviewed >= 1
    assert final.confidence in {"HIGH", "MEDIUM", "LOW"}
    assert "because of guidance" not in final.why_it_likely_happened.lower()


def test_graph_respects_phase3_max_iterations():
    investigation_graph.reset_investigation_graph()
    ranked = _ranked()
    evidence = [
        _price(),
        _item("news", "Guidance cut chatter", published="2026-08-20"),
        _item("news", "Second headline", published="2026-08-20"),
        _item("filing", "8-K", "Item 2.02", url="https://www.sec.gov/a", published="2026-08-20"),
    ]
    collect = AsyncMock(return_value=evidence)
    da_high = {
        "hypotheses": ranked["hypotheses"],
        "outcome": "confidence_cut",
        "counterargument": "Need IR.",
        "leading_weakened": True,
        "reversal": False,
        "notes": [],
        "confidence_delta": -0.1,
        "challenge_severity": "HIGH",
        "recommendation": "RESEARCH_MORE",
        "additional_evidence_needed": ["earnings transcript from IR"],
        "devils_advocate_result": {
            "additional_evidence_needed": ["earnings transcript from IR"],
            "challenge_severity": "HIGH",
            "recommendation": "RESEARCH_MORE",
            "strongest_counterargument": "Need IR.",
        },
    }

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
        ), patch(
            "app.services.investigation_runner._collect_evidence",
            new=collect,
        ), patch(
            "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
            new=AsyncMock(return_value=ranked),
        ), patch(
            "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "rejected": [],
                    "notes": ["grounded"],
                    "citation_coverage": 1.0,
                }
            ),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=da_high),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=74,
                max_iterations=1,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert collect.await_count == 1
    assert result.challenge_loops == 0
    assert result.status == "complete"
    assert result.final_report.get("what_happened") or result.final_report.get("final_assessment") is not None


def test_mocked_nvda_investigation_end_to_end():
    investigation_graph.reset_investigation_graph()
    items = [
        _price(),
        evidence_graph.enrich_item(_ir_guidance(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_revenue_beat(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_annual_unchanged(), event_time=EVENT_TIME),
        evidence_graph.enrich_item(_sector(), event_time=EVENT_TIME),
    ]
    ranked = _ranked("Guidance concerns caused the decline.")

    async def _verify(ticker, hyps, evidence, **kwargs):
        return await investigation_verification_agent.verify_hypotheses(
            ticker, hyps, evidence, event_time=EVENT_TIME
        )

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
        ), patch(
            "app.services.investigation_runner._collect_evidence",
            new=AsyncMock(return_value=items),
        ), patch(
            "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
            new=AsyncMock(return_value=ranked),
        ), patch(
            "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
            new=_verify,
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(
                return_value={
                    "hypotheses": ranked["hypotheses"],
                    "outcome": "held",
                    "counterargument": "Revenue beat and sector weakness remain plausible.",
                    "leading_weakened": False,
                    "reversal": False,
                    "notes": [],
                    "confidence_delta": 0.0,
                    "challenge_severity": "MEDIUM",
                    "recommendation": "REVIEW",
                    "contradictory_evidence": ["NVIDIA revenue beat estimates"],
                    "additional_evidence_needed": [],
                    "devils_advocate_result": {
                        "strongest_counterargument": "Revenue beat and sector weakness remain plausible.",
                        "challenge_severity": "MEDIUM",
                        "recommendation": "REVIEW",
                        "additional_evidence_needed": [],
                        "contradictory_evidence": ["NVIDIA revenue beat estimates"],
                    },
                }
            ),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=75,
                event={"occurred_at": "2026-08-20T16:00:00+00:00"},
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.status == "complete"
    lead = result.hypotheses[0]["statement"].lower()
    assert "because of guidance" not in lead
    assert "hallucinated" not in lead
    report = result.final_report
    assert report.get("contradicting_evidence")
    assert report.get("evidence_gaps") is not None
    assert report.get("trading_advice") == ""
    assert "buy" not in (report.get("final_assessment") or "").lower()
    assert result.challenge_loops == 0

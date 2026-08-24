"""Phase 4 — hypothesis engine and causal investigation."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase4.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_GRAPH_ENABLED"] = "true"
os.environ["MAX_HYPOTHESES"] = "6"

import asyncio  # noqa: E402

from app.agents.hypothesis_generator import catalog_hypotheses  # noqa: E402
from app.agents.investigation_planner_agent import tasks_from_tools  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.models.hypothesis_schemas import HypothesisRecord  # noqa: E402
from app.services import hypothesis_engine  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()

EVENT_TIME = datetime(2024, 6, 10, 16, 0, tzinfo=timezone.utc)


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


def _hyp(
    hid: str,
    title: str,
    category: str,
    **kwargs,
) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id=hid,
        title=title,
        category=category,  # type: ignore[arg-type]
        **kwargs,
    )


def _item(
    source_type: str,
    title: str,
    excerpt: str = "",
    url: str = "https://example.com/a",
    published=None,
    extra=None,
) -> dict:
    payload = dict(extra or {})
    if published is not None:
        payload["date"] = published
    return {
        "source_type": source_type,
        "retrieval_method": "mcp",
        "title": title,
        "excerpt": excerpt or title,
        "source_url": url,
        "raw_payload": payload,
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def test_generator_returns_3_to_6_hypotheses():
    records = catalog_hypotheses(_move(), asset_hint="equity")
    assert 3 <= len(records) <= 6
    cats = {r.category for r in records}
    assert cats <= {
        "COMPANY_SPECIFIC",
        "EARNINGS",
        "GUIDANCE",
        "MACRO",
        "REGULATORY",
        "COMPETITOR",
        "INDUSTRY",
        "MARKET_WIDE",
        "TECHNICAL",
        "SENTIMENT",
        "OTHER",
    }
    assert all(r.required_evidence for r in records)
    assert all(r.hypothesis_id.startswith("H") for r in records)


def test_generator_dedupes_duplicate_seeds():
    records = catalog_hypotheses(
        _move(),
        seeds=[
            "Guidance / earnings disappointment",
            "Guidance / earnings disappointment",
            "Broad market / sector selloff",
        ],
    )
    titles = [hypothesis_engine._norm_text(r.title) for r in records]
    assert len(titles) == len(set(titles))


def test_research_tasks_reference_hypotheses():
    records = [r.model_dump() for r in catalog_hypotheses(_move())]
    tasks = tasks_from_tools(
        ["get_stock_news", "get_recent_filings", "get_stock_price"],
        hypothesis_count=len(records),
        hypothesis_records=records,
    )
    assert tasks
    assert all(t.hypothesis_ids for t in tasks)
    filing = next(t for t in tasks if t.required_tool == "get_recent_filings")
    assert any(hid.startswith("H") for hid in filing.hypothesis_ids)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_supporting_contradicting_neutral():
    guidance = _hyp("H1", "Guidance concerns", "GUIDANCE")
    macro = _hyp("H2", "Macro selloff", "MARKET_WIDE")
    support = hypothesis_engine.classify_evidence_item(
        _item("news", "NVIDIA reduced next-quarter gross-margin guidance"),
        guidance,
        index=0,
        event_time=EVENT_TIME,
    )
    contra = hypothesis_engine.classify_evidence_item(
        _item("news", "Revenue beat estimates and guidance unchanged"),
        guidance,
        index=1,
        event_time=EVENT_TIME,
    )
    neutral = hypothesis_engine.classify_evidence_item(
        _item("news", "Analyst preview of the semiconductor calendar"),
        guidance,
        index=2,
        event_time=EVENT_TIME,
    )
    insufficient = hypothesis_engine.classify_evidence_item(
        {
            "source_type": "other",
            "title": "get_stock_news unavailable",
            "excerpt": "timeout",
            "source_url": "",
            "raw_payload": {"error": "timeout"},
        },
        guidance,
        index=3,
        event_time=EVENT_TIME,
    )
    assert support.relation == "supports"
    assert contra.relation == "contradicts"
    assert neutral.relation == "neutral"
    assert insufficient.relation == "insufficient"
    price_vs_macro = hypothesis_engine.classify_evidence_item(
        _item("price", "NVDA 1d move"),
        macro,
        index=4,
        move=_move(),
    )
    assert price_vs_macro.relation == "contradicts"


# ---------------------------------------------------------------------------
# Scoring / independence / quality
# ---------------------------------------------------------------------------


def test_support_raises_score_contradiction_lowers_it():
    record = _hyp(
        "H1",
        "Guidance concerns",
        "GUIDANCE",
        required_evidence=["current guidance"],
    )
    empty = hypothesis_engine.score_and_rank(
        [record], [], event_time=EVENT_TIME, allow_new=False
    )
    baseline = empty.hypotheses[0].support_score
    supported = hypothesis_engine.score_and_rank(
        [record],
        [_item("ir_page", "Company cut guidance", url="https://investor.nvidia.com/8k")],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    both = hypothesis_engine.score_and_rank(
        [record],
        [
            _item("ir_page", "Company cut guidance", url="https://investor.nvidia.com/8k"),
            _item("news", "Revenue beat estimates and guidance unchanged"),
        ],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    assert supported.hypotheses[0].support_score > baseline
    assert both.hypotheses[0].support_score < supported.hypotheses[0].support_score


def test_primary_source_outweighs_news():
    record = _hyp("H1", "Guidance concerns", "GUIDANCE")
    news = hypothesis_engine.score_and_rank(
        [record],
        [_item("news", "NVIDIA cut guidance")],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    primary = hypothesis_engine.score_and_rank(
        [record],
        [_item("filing", "8-K cut guidance", url="https://www.sec.gov/Archives/x")],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    assert primary.hypotheses[0].support_score > news.hypotheses[0].support_score
    assert hypothesis_engine.source_quality(
        _item("filing", "8-K")
    ) == "PRIMARY"


def test_repeated_sources_are_not_independent():
    record = _hyp("H1", "Guidance concerns", "GUIDANCE")
    release = _item(
        "filing",
        "NVIDIA 8-K earnings release",
        url="https://www.sec.gov/Archives/nvda-8k",
    )
    reprints = [
        _item(
            "news",
            "NVIDIA 8-K earnings release",
            url=f"https://www.reuters.com/article/{i}",
        )
        for i in range(5)
    ]
    once = hypothesis_engine.score_and_rank(
        [record], [release], event_time=EVENT_TIME, allow_new=False
    )
    many = hypothesis_engine.score_and_rank(
        [record], [release, *reprints], event_time=EVENT_TIME, allow_new=False
    )
    delta = many.hypotheses[0].support_score - once.hypotheses[0].support_score
    assert delta < 0.4


# ---------------------------------------------------------------------------
# Ranking / dynamic updates
# ---------------------------------------------------------------------------


def test_ranking_identifies_leader_and_alternative():
    records = [
        _hyp("H1", "Guidance concerns", "GUIDANCE"),
        _hyp("H2", "Macro selloff", "MARKET_WIDE"),
        _hyp("H3", "Regulatory restrictions", "REGULATORY"),
        _hyp("H4", "Competitor announcement", "COMPETITOR"),
    ]
    evidence = [
        _item("ir_page", "NVIDIA reduced next-quarter gross-margin guidance", url="https://investor.nvidia.com/"),
        _item("price", "NVDA vs SPY residual"),
        _item("news", "No regulatory action in today's tape"),
    ]
    result = hypothesis_engine.score_and_rank(
        records, evidence, event_time=EVENT_TIME, move=_move(), allow_new=False
    )
    assert result.leading_hypothesis_id == "H1"
    assert result.strongest_alternative_id
    assert result.strongest_alternative_id != result.leading_hypothesis_id
    ordered = [r.hypothesis_id for r in result.hypotheses if r.status != "DISCARDED"]
    assert ordered[0] == "H1"


def test_new_evidence_can_reorder_and_weaken():
    records = [
        _hyp("H1", "Guidance concerns", "GUIDANCE"),
        _hyp("H2", "Macro selloff", "MARKET_WIDE"),
    ]
    first = hypothesis_engine.score_and_rank(
        records,
        [_item("ir_page", "NVIDIA cut guidance", url="https://investor.nvidia.com/")],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    assert first.leading_hypothesis_id == "H1"
    assert first.hypotheses[0].status in {"SUPPORTED", "INVESTIGATING", "WEAKENED"}
    second = hypothesis_engine.score_and_rank(
        first.hypotheses,
        [
            _item("ir_page", "NVIDIA cut guidance", url="https://investor.nvidia.com/"),
            _item("news", "Revenue beat estimates and guidance unchanged"),
            _item("news", "SPY broad market selloff dragged semiconductors"),
        ],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    lead = next(r for r in second.hypotheses if r.hypothesis_id == "H1")
    assert lead.status in {"WEAKENED", "CONTRADICTED", "SUPPORTED"}
    if lead.status == "SUPPORTED":
        assert lead.contradicting_evidence


def test_can_create_new_hypothesis_within_limit():
    records = [
        _hyp("H1", "Guidance concerns", "GUIDANCE"),
        _hyp("H2", "Macro selloff", "MARKET_WIDE"),
    ]
    result = hypothesis_engine.score_and_rank(
        records,
        [_item("news", "A major competitor announced a new product launch")],
        event_time=EVENT_TIME,
        max_hypotheses=6,
    )
    assert "H3" in result.created_hypothesis_ids
    assert any(r.category == "COMPETITOR" for r in result.hypotheses)
    capped = hypothesis_engine.maybe_add_hypotheses(
        result.hypotheses,
        [_item("news", "Commerce Department export restriction announced")],
        max_hypotheses=3,
    )
    assert capped[1] == []


def test_discarded_is_preserved_not_deleted():
    record = _hyp(
        "H3",
        "Regulatory restrictions",
        "REGULATORY",
        required_evidence=["SEC filings", "official government or company statements"],
    )
    result = hypothesis_engine.score_and_rank(
        [record],
        [_item("news", "No regulatory action and license approved for shipments")],
        event_time=EVENT_TIME,
        allow_new=False,
    )
    discarded = [r for r in result.hypotheses if r.status == "DISCARDED"]
    assert discarded
    assert discarded[0].discard_reason


def test_status_vocabulary():
    statuses = set()
    open_r = hypothesis_engine.score_and_rank(
        [_hyp("H1", "Guidance", "GUIDANCE")],
        [],
        allow_new=False,
    )
    statuses.add(open_r.hypotheses[0].status)
    investigating = hypothesis_engine.score_and_rank(
        [_hyp("H1", "Guidance", "GUIDANCE")],
        [_item("news", "Analyst preview of the semiconductor calendar")],
        allow_new=False,
    )
    statuses.add(investigating.hypotheses[0].status)
    supported = hypothesis_engine.score_and_rank(
        [_hyp("H1", "Guidance", "GUIDANCE")],
        [_item("filing", "8-K cut guidance", url="https://www.sec.gov/x")],
        allow_new=False,
    )
    statuses.add(supported.hypotheses[0].status)
    assert "OPEN" in statuses or "INSUFFICIENT_EVIDENCE" in statuses
    assert statuses & {
        "SUPPORTED",
        "WEAKENED",
        "INVESTIGATING",
        "INSUFFICIENT_EVIDENCE",
        "CONTRADICTED",
        "DISCARDED",
    }


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def test_temporal_labels_and_retrospective_not_causal():
    record = _hyp("H1", "Guidance concerns", "GUIDANCE")
    items = [
        _item("news", "NVIDIA cut guidance", published="2024-06-09"),
        _item("news", "NVIDIA cut guidance", published="2024-06-10", url="https://example.com/b"),
        _item("news", "NVIDIA cut guidance", published="2024-06-11", url="https://example.com/c"),
        _item("news", "NVIDIA cut guidance", published="2024-06-20", url="https://example.com/d"),
    ]
    labels = [
        hypothesis_engine.temporal_label(item, EVENT_TIME) for item in items
    ]
    assert labels == ["pre_event", "same_day", "post_event", "retrospective"]
    same = hypothesis_engine.score_and_rank(
        [record], [items[1]], event_time=EVENT_TIME, allow_new=False
    )
    late = hypothesis_engine.score_and_rank(
        [record], [items[3]], event_time=EVENT_TIME, allow_new=False
    )
    assert same.hypotheses[0].support_score > late.hypotheses[0].support_score


# ---------------------------------------------------------------------------
# Devil's Advocate / integration
# ---------------------------------------------------------------------------


def test_devils_advocate_receives_hypothesis_context():
    from app.agents import devils_advocate_agent

    captured = {}

    async def fake_llm(**kwargs):
        captured["prompt"] = kwargs.get("user_prompt") or ""
        return {
            "counterargument": "Sector beta remains plausible.",
            "materially_weakened": False,
            "confidence_delta": 0.0,
            "promote_competitor_index": None,
            "notes": [],
            "overweighted_evidence": "Single IR page",
            "ignored_evidence": "SPY also fell",
            "alternative_more_plausible": False,
            "additional_evidence_needed": "Transcript",
        }

    hyps = [
        {
            "statement": "Guidance concerns",
            "stance": "supports_move",
            "weight": 0.6,
            "confidence_score": 0.6,
            "evidence_indices": [0],
        },
        {
            "statement": "Macro selloff",
            "stance": "market_noise",
            "weight": 0.4,
            "confidence_score": 0.4,
            "evidence_indices": [1],
        },
    ]
    context = {
        "leading_hypothesis_id": "H1",
        "strongest_alternative_id": "H2",
        "supporting_evidence": [{"title": "Cut guidance", "quality": "PRIMARY"}],
        "contradicting_evidence": [{"title": "Revenue beat", "quality": "MEDIUM"}],
        "missing_evidence": ["earnings transcript"],
        "source_quality": ["PRIMARY"],
        "confidence_level": "MEDIUM",
        "support_score": 0.58,
    }

    async def _run():
        with patch(
            "app.agents.devils_advocate_agent.call_openai_json",
            new=fake_llm,
        ):
            return await devils_advocate_agent.stress_test_leading(
                _move(),
                hyps,
                [
                    _item("ir_page", "Cut guidance"),
                    _item("price", "move"),
                ],
                hypothesis_context=context,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert "hypothesis_context" in captured["prompt"]
    assert "H1" in captured["prompt"]
    assert result["hypothesis_context"]["leading_hypothesis_id"] == "H1"
    assert result["additional_evidence_needed"]


def test_legacy_ranker_output_includes_support_not_probability():
    plan = {
        "focus": "NVDA",
        "seed_hypotheses": ["Guidance concerns", "Macro selloff"],
        "hypothesis_records": [
            r.model_dump()
            for r in [
                _hyp("H1", "Guidance concerns", "GUIDANCE"),
                _hyp("H2", "Macro selloff", "MARKET_WIDE"),
            ]
        ],
    }
    evidence = [_item("ir_page", "NVIDIA cut guidance", url="https://investor.nvidia.com/")]

    async def _run():
        from app.agents import hypothesis_ranker_agent

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
                            "devil_advocate_notes": "",
                            "evidence_indices": [0],
                        }
                    ],
                }
            ),
        ):
            return await hypothesis_ranker_agent.rank_hypotheses(_move(), plan, evidence)

    ranked = asyncio.get_event_loop().run_until_complete(_run())
    assert "not a probability" in (ranked.get("summary") or "").lower() or ranked["hypotheses"][0].get("support_score") is not None
    assert ranked["hypotheses"][0].get("confidence_level")
    assert "engine" in ranked

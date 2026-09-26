"""Phase 3 — LangGraph investigation orchestrator."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase3.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_GRAPH_ENABLED"] = "true"
os.environ["MAX_INVESTIGATION_ITERATIONS"] = "2"
os.environ["INVESTIGATION_TOOL_RETRIES"] = "1"
os.environ["INVESTIGATION_RESEARCH_CONCURRENCY"] = "2"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.agents import investigation_planner_agent  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_plan_schemas import ResearchPlan  # noqa: E402
from app.services import evidence_assessment, evidence_ledger_store, investigation_runner  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.workflows import investigation_graph  # noqa: E402

init_db()
client = TestClient(app)
GUEST = {"X-Guest-Id": "phase3guestaaaaaaaaaaaaaaaaaaaa"}


def _move(ticker: str = "NVDA", pct: float = -8.7) -> MoveSnapshot:
    return MoveSnapshot(
        ticker=ticker,
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=109.5,
        is_noise=False,
        source="test",
        detail=f"{ticker} moved {pct}% over 1d",
        depth="deep",
        residual_pct=-7.7,
        move_zscore=-4.0,
    )


def _plan() -> dict:
    return {
        "focus": "Why NVDA fell 8.7%",
        "tools": ["get_stock_news"],
        "seed_hypotheses": [
            "Guidance concerns",
            "Macro selloff",
            "Regulatory restriction",
            "Competitor announcement",
        ],
        "skip_reason": None,
        "need_browser_ir": False,
    }


def _hypotheses(statement: str = "Guidance concerns drove the drop") -> dict:
    hyps = [
        {
            "statement": statement,
            "stance": "supports_move",
            "confidence_score": 0.81,
            "weight": 0.55,
            "devil_advocate_notes": "Could still be sector beta.",
            "evidence_indices": [1],
            "rank": 1,
        },
        {
            "statement": "Macro selloff",
            "stance": "market_noise",
            "confidence_score": 0.37,
            "weight": 0.3,
            "devil_advocate_notes": "Residual vs SPY is large.",
            "evidence_indices": [0],
            "rank": 2,
        },
        {
            "statement": "Regulatory restriction",
            "stance": "unknown",
            "confidence_score": 0.18,
            "weight": 0.15,
            "devil_advocate_notes": "No filing cited yet.",
            "evidence_indices": [0],
            "rank": 3,
        },
    ]
    return {
        "summary": "Guidance is the leading support score.",
        "hypotheses": hyps,
    }


def _verified(ranked: dict) -> dict:
    return {
        "hypotheses": ranked["hypotheses"],
        "rejected": [],
        "notes": ["All claims grounded."],
        "citation_coverage": 1.0,
    }


def _da(ranked: dict) -> dict:
    return {
        "hypotheses": ranked["hypotheses"],
        "outcome": "held",
        "counterargument": "A broad semiconductor drawdown remains plausible.",
        "leading_weakened": False,
        "reversal": False,
        "notes": ["Alternative: macro/sector beta."],
        "confidence_delta": 0.0,
    }


def _price_item() -> dict:
    return {
        "source_type": "price",
        "retrieval_method": "system",
        "title": "NVDA 1d move",
        "excerpt": "down 8.7%",
        "source_url": "",
        "raw_payload": {},
    }


def _news(title: str, extra: str = "") -> dict:
    return {
        "source_type": "news",
        "retrieval_method": "mcp",
        "title": title,
        "excerpt": extra or title,
        "source_url": "https://example.com/n",
        "raw_payload": {"tool": "get_stock_news"},
    }


def _filing() -> dict:
    return {
        "source_type": "filing",
        "retrieval_method": "mcp",
        "title": "NVDA 8-K",
        "excerpt": "Item 2.02 results",
        "source_url": "https://www.sec.gov/cgi-bin/browse-edgar",
        "raw_payload": {"tool": "get_recent_filings"},
    }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def test_planner_builds_valid_structured_plan():
    structured = investigation_planner_agent.research_plan_from_legacy(
        _plan(), move=_move()
    )
    assert isinstance(structured, ResearchPlan)
    assert structured.hypotheses
    assert structured.tasks
    assert "get_stock_news" in structured.tools
    validated = investigation_planner_agent.validate_research_plan(
        structured.model_dump()
    )
    assert validated.investigation_question


def test_planner_rejects_missing_hypotheses():
    try:
        investigation_planner_agent.validate_research_plan(
            {"hypotheses": [], "tasks": [], "tools": []}
        )
    except ValueError as exc:
        assert "hypotheses" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for missing hypotheses")


def test_planner_rejects_missing_research_tasks():
    try:
        investigation_planner_agent.validate_research_plan(
            {"hypotheses": ["Guidance"], "tasks": [], "tools": []}
        )
    except ValueError as exc:
        assert "task" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for missing tasks")


def test_planner_rejects_malformed_task():
    try:
        investigation_planner_agent.validate_research_plan(
            {
                "hypotheses": ["Guidance"],
                "tasks": [{"task_id": "T1", "objective": "broken"}],
            }
        )
    except (ValueError, ValidationError):
        return
    raise AssertionError("expected validation error for malformed task")


def test_fallback_plan_is_not_every_tool():
    fallback = investigation_planner_agent._fallback_plan(_move(), asset_hint="equity")
    assert "get_stock_news" in fallback["tools"]
    assert set(fallback["tools"]) <= investigation_planner_agent.VALID_TOOLS
    # Deep -8.7% equity can add IR, but not every catalog tool.
    assert "get_price_history" not in fallback["tools"] or _move().window_label != "1d"


# ---------------------------------------------------------------------------
# Evidence assessment
# ---------------------------------------------------------------------------


def test_assessment_marks_thin_evidence_insufficient():
    result = evidence_assessment.assess_evidence(
        [_price_item(), _news("Only one headline")],
        hypotheses=["Guidance concerns", "Macro selloff"],
        used_tools=["get_stock_news"],
        asset_hint="equity",
    )
    assert result.sufficient_evidence is False
    assert "get_recent_filings" in result.recommended_next_tools
    assert result.weakened_hypotheses


def test_assessment_sufficient_with_filing_and_news():
    result = evidence_assessment.assess_evidence(
        [_price_item(), _news("Export news"), _news("Second headline"), _filing()],
        hypotheses=["Guidance concerns"],
        used_tools=["get_stock_news", "get_recent_filings"],
    )
    assert result.sufficient_evidence is True
    assert result.supported_hypotheses


def test_assessment_detects_contradictory_titles():
    result = evidence_assessment.assess_evidence(
        [
            _news("NVDA beat estimates", "beat"),
            _news("Analysts flag a miss", "miss"),
            _filing(),
        ]
    )
    assert result.contradictions


def test_duplicate_evidence_is_collapsed():
    rows = investigation_graph._dedupe_evidence(
        [_news("Same"), _news("Same"), _news("Other")]
    )
    titles = [r["title"] for r in rows]
    assert titles.count("Same") == 1
    assert "Other" in titles


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def test_graph_normal_investigation():
    ranked = _hypotheses()
    evidence = [_price_item(), _news("Guidance cut chatter"), _news("Second"), _filing()]

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
            new=AsyncMock(return_value=_verified(ranked)),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=_da(ranked)),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=1,
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.status == "complete"
    assert result.hypotheses
    assert result.hypotheses[0]["evidence_indices"]
    assert result.da.get("counterargument")
    assert result.iteration_count >= 1


def test_graph_loops_when_evidence_is_thin():
    ranked = _hypotheses()
    collect = AsyncMock(
        side_effect=[
            [_price_item(), _news("Only one headline")],
            [_price_item(), _news("Only one headline"), _news("Second"), _filing()],
        ]
    )

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
            new=AsyncMock(return_value=_verified(ranked)),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=_da(ranked)),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=2,
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert collect.await_count == 2
    assert result.iteration_count == 2
    assert result.status == "complete"
    second_plan = collect.await_args_list[1].args[1]
    assert "get_recent_filings" in (second_plan.get("tools") or [])


def test_graph_stops_at_max_iterations():
    ranked = _hypotheses()
    collect = AsyncMock(return_value=[_price_item(), _news("Still thin")])

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
            new=AsyncMock(return_value=_verified(ranked)),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=_da(ranked)),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=3,
                max_iterations=2,
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert collect.await_count == 2
    assert result.iteration_count == 2
    assert result.incomplete_evidence is True
    assert result.status == "complete"


def test_graph_continues_after_failed_tool():
    ranked = _hypotheses()
    failed = {
        "source_type": "other",
        "retrieval_method": "mcp",
        "title": "get_stock_news unavailable",
        "excerpt": "timeout",
        "source_url": "",
        "raw_payload": {"tool": "get_stock_news", "error": "timeout"},
    }
    collect = AsyncMock(
        side_effect=[
            [_price_item(), failed],
            [_price_item(), failed, _filing(), _news("Recovered"), _news("Second")],
        ]
    )

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
            new=AsyncMock(return_value=_verified(ranked)),
        ), patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=_da(ranked)),
        ):
            return await investigation_graph.run_investigation_graph(
                _move(), ticker="NVDA", investigation_id=4, max_iterations=2
            )

    result = asyncio.get_event_loop().run_until_complete(_run())
    assert result.status == "complete"
    assert any("failed" in (e or "").lower() or "unavailable" in (e or "").lower() for e in result.errors) or result.assessment.get("failed_tools")


def test_tool_retry_then_success():
    calls = {"n": 0}

    async def flaky(ticker: str, tool: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                {
                    "source_type": "other",
                    "retrieval_method": "mcp",
                    "title": f"{tool} unavailable",
                    "excerpt": "timeout",
                    "source_url": "",
                    "raw_payload": {"tool": tool, "error": "timeout"},
                }
            ]
        return [_news("Recovered headline")]

    async def _run():
        with patch(
            "app.services.investigation_runner._evidence_from_tool",
            new=flaky,
        ):
            return await investigation_runner._evidence_from_tool_with_retry(
                "NVDA", "get_stock_news"
            )

    items = asyncio.get_event_loop().run_until_complete(_run())
    assert calls["n"] == 2
    assert items[0]["source_type"] == "news"


def test_permanent_tool_failure_is_recorded():
    async def always_fail(ticker: str, tool: str):
        return [
            {
                "source_type": "other",
                "retrieval_method": "mcp",
                "title": f"{tool} unavailable",
                "excerpt": "401",
                "source_url": "",
                "raw_payload": {"tool": tool, "error": "401"},
            }
        ]

    async def _run():
        with patch(
            "app.services.investigation_runner._evidence_from_tool",
            new=always_fail,
        ):
            return await investigation_runner._evidence_from_tool_with_retry(
                "NVDA", "get_stock_news"
            )

    items = asyncio.get_event_loop().run_until_complete(_run())
    assert items[0]["raw_payload"]["error"] == "401"


def test_verification_keeps_supported_drops_unsupported():
    from app.agents import investigation_verification_agent

    hyps = [
        {
            "statement": "Supported by news",
            "stance": "supports_move",
            "confidence_score": 0.7,
            "weight": 0.7,
            "evidence_indices": [1],
        },
        {
            "statement": "Invented claim with no receipt",
            "stance": "supports_move",
            "confidence_score": 0.9,
            "weight": 0.3,
            "evidence_indices": [],
        },
    ]
    evidence = [_price_item(), _news("Supported by news")]
    result = asyncio.get_event_loop().run_until_complete(
        investigation_verification_agent.verify_hypotheses("NVDA", hyps, evidence)
    )
    statements = [h["statement"] for h in result["hypotheses"]]
    assert "Supported by news" in statements
    assert all("Invented" not in s for s in statements) or result.get("rejected")


def test_devils_advocate_returns_alternative():
    from app.agents import devils_advocate_agent

    ranked = _hypotheses()
    result = asyncio.get_event_loop().run_until_complete(
        devils_advocate_agent.stress_test_leading(
            _move(), ranked["hypotheses"], [_price_item(), _news("Guidance cut")]
        )
    )
    assert result.get("counterargument") or result.get("outcome")
    assert "hypotheses" in result


# ---------------------------------------------------------------------------
# Persistence / API compatibility
# ---------------------------------------------------------------------------


def _pipeline_patches(evidence, ranked):
    return [
        patch(
            "app.services.investigation_runner.detect_move",
            new=AsyncMock(return_value=_move()),
        ),
        patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(return_value=_plan()),
        ),
        patch(
            "app.services.investigation_runner._collect_evidence",
            new=AsyncMock(return_value=evidence),
        ),
        patch(
            "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
            new=AsyncMock(return_value=ranked),
        ),
        patch(
            "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
            new=AsyncMock(return_value=_verified(ranked)),
        ),
        patch(
            "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
            new=AsyncMock(return_value=_da(ranked)),
        ),
    ]


def test_investigate_api_persists_once():
    ranked = _hypotheses()
    evidence = [_price_item(), _news("Export"), _news("Second"), _filing()]
    patches = _pipeline_patches(evidence, ranked)
    for p in patches:
        p.start()
    try:
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={"ticker": "nvda", "window_label": "1d"},
        )
    finally:
        for p in patches:
            p.stop()
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "complete"
    assert body["ticker"] == "NVDA"
    assert body["claims_count"] == 3
    assert body["evidence_count"] == 4
    assert body["claims"][0]["evidence_links"]
    assert body.get("da_outcome", {}).get("outcome") == "held"


def test_rerun_does_not_duplicate_investigation():
    ranked = _hypotheses()
    evidence = [_price_item(), _news("Export"), _news("Second"), _filing()]
    created = client.post(
        "/api/investigations",
        headers=GUEST,
        json={"ticker": "MSFT", "trigger_reason": "on_demand"},
    )
    assert created.status_code == 201, created.text
    inv_id = created.json()["id"]

    def _run_once():
        patches = _pipeline_patches(evidence, ranked)
        for p in patches:
            p.start()
        try:
            return client.post(
                f"/api/investigations/{inv_id}/run",
                headers=GUEST,
                json={"window_label": "1d"},
            )
        finally:
            for p in patches:
                p.stop()

    first = _run_once()
    second = _run_once()
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"] == inv_id
    assert second.json()["claims_count"] == first.json()["claims_count"]
    assert second.json()["evidence_count"] == first.json()["evidence_count"]

    db = SessionLocal()
    try:
        detail = evidence_ledger_store.get_investigation(
            db, owner_key="guest:phase3guestaaaaaaaaaaaaaaaaaaaa", investigation_id=inv_id
        )
        assert detail is not None
        assert detail.claims_count == 3
        assert detail.evidence_count == 4
        assert detail.claims[0].evidence_links
    finally:
        db.close()


def test_create_then_list_shape_unchanged():
    created = client.post(
        "/api/investigations",
        headers=GUEST,
        json={"ticker": "AAPL", "trigger_reason": "on_demand"},
    )
    assert created.status_code == 201
    listed = client.get("/api/investigations", headers=GUEST)
    assert listed.status_code == 200
    rows = listed.json()
    assert any(r["ticker"] == "AAPL" for r in rows)
    row = next(r for r in rows if r["ticker"] == "AAPL")
    assert "status" in row
    assert "claims_count" in row

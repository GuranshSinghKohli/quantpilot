"""Phase 8 — company memory and longitudinal intelligence."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase8_memory.db"
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
from app.models.investigation_schemas import InvestigationCreateRequest  # noqa: E402
from app.services import company_memory, evidence_ledger_store  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.workflows import investigation_graph  # noqa: E402

init_db()
client = TestClient(app)
OWNER_A = "guest:phase8owneraaaaaaaaaaaaaaaaaa"
OWNER_B = "guest:phase8ownerbbbbbbbbbbbbbbbbbb"
GUEST_A = {"X-Guest-Id": "phase8owneraaaaaaaaaaaaaaaaaa"}
EVENT_TIME = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)


def _move(pct: float = -8.7) -> MoveSnapshot:
    return MoveSnapshot(
        ticker="NVDA",
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=100.0 * (1 - pct / 100.0),
        is_noise=False,
        source="test",
        detail=f"NVDA moved {pct}%",
        residual_pct=pct + 1,
        move_zscore=-4.0,
        depth="deep",
    )


def _create_case(db, owner: str, ticker: str = "NVDA"):
    return evidence_ledger_store.create_investigation(
        db,
        owner_key=owner,
        user=None,
        body=InvestigationCreateRequest(
            ticker=ticker,
            trigger_reason="on_demand",
            summary=f"Investigating {ticker}.",
        ),
    )


def _ir() -> dict:
    return {
        "source_type": "ir_page",
        "retrieval_method": "mcp",
        "title": "NVIDIA earnings release",
        "excerpt": "Management expects continued strong AI infrastructure demand.",
        "source_url": "https://investor.nvidia.com/q1",
        "raw_payload": {"date": "2026-02-20"},
    }


# ---------------------------------------------------------------------------
# Creation / provenance / temporal / dedup / updates
# ---------------------------------------------------------------------------


def test_memory_types_require_source_and_timestamp():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        ev = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER_A,
            investigation_id=case.id,
            body=__import__(
                "app.models.investigation_schemas", fromlist=["EvidenceCreateRequest"]
            ).EvidenceCreateRequest(
                source_type="ir_page",
                retrieval_method="mcp",
                title="NVIDIA earnings",
                excerpt="Management expects continued strong AI infrastructure demand.",
                source_url="https://investor.nvidia.com/q1",
            ),
        )
        stored = company_memory.extract_and_store(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            investigation_id=case.id,
            hypotheses=[
                {
                    "statement": "Export restrictions are a recurring regulatory risk.",
                    "claim_type": "RISK",
                    "evidence_indices": [0],
                    "supporting_indices": [0],
                },
                {
                    "statement": "Revenue increased 12%.",
                    "claim_type": "FACT",
                    "evidence_indices": [0],
                    "supporting_indices": [0],
                },
            ],
            evidence=[_ir()],
            evidence_ids=[ev.id],
            move_pct=-8.7,
            occurred_at=EVENT_TIME,
        )
        types = {i.memory_type for i in stored}
        assert "EVENT" in types
        assert "RISK" in types
        assert "COMPANY_FACT" in types
        assert "INVESTIGATION_CONCLUSION" in types
        assert "MANAGEMENT_STATEMENT" in types
        assert all(i.investigation_id == case.id for i in stored)
        assert all(i.observed_at is not None for i in stored)
        rejected, _ = company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "COMPANY_FACT",
                "statement": "Hallucinated fact with no backing.",
            },
        )
        assert rejected is None
    finally:
        db.close()


def test_missing_evidence_candidate_is_rejected():
    ok = company_memory._quality_ok(
        {
            "memory_type": "COMPANY_FACT",
            "statement": "NVIDIA sells GPUs into data centers worldwide today.",
            "observed_at": EVENT_TIME,
        }
    )
    assert ok is False


def test_old_statement_preserved_when_new_arrives():
    owner = "guest:phase8trendcccccccccccccccccc"
    db = SessionLocal()
    try:
        case = _create_case(db, owner)
        q1, _ = company_memory.upsert_memory(
            db,
            owner_key=owner,
            candidate={
                "ticker": "NVDA",
                "memory_type": "MANAGEMENT_STATEMENT",
                "statement": "AI demand remains strong.",
                "theme_key": "ai_demand",
                "polarity": "POSITIVE",
                "observed_at": datetime(2026, 2, 20, tzinfo=timezone.utc),
                "investigation_id": case.id,
                "source_type": "ir_page",
            },
        )
        q2, _ = company_memory.upsert_memory(
            db,
            owner_key=owner,
            candidate={
                "ticker": "NVDA",
                "memory_type": "MANAGEMENT_STATEMENT",
                "statement": "AI demand remains strong this quarter.",
                "theme_key": "ai_demand",
                "polarity": "POSITIVE",
                "observed_at": datetime(2026, 5, 20, tzinfo=timezone.utc),
                "investigation_id": case.id,
                "source_type": "ir_page",
            },
        )
        q3, action = company_memory.upsert_memory(
            db,
            owner_key=owner,
            candidate={
                "ticker": "NVDA",
                "memory_type": "MANAGEMENT_STATEMENT",
                "statement": "Customers are becoming more selective.",
                "theme_key": "ai_demand",
                "polarity": "NEGATIVE",
                "observed_at": datetime(2026, 8, 20, tzinfo=timezone.utc),
                "investigation_id": case.id,
                "source_type": "ir_page",
            },
        )
        db.refresh(q1)
        db.refresh(q2)
        assert q1 is not None and q3 is not None
        assert q1.id != q3.id
        assert q1.statement == "AI demand remains strong."
        assert q1.status == "OUTDATED"
        assert q2.status == "OUTDATED"
        assert q3.status == "ACTIVE"
        assert q3.supersedes_id in {q1.id, q2.id}
        assert action == "CREATED"
        items = company_memory._load_active(db, owner, "NVDA", include_outdated=True)
        trend = company_memory.detect_management_trend(items)
        assert "weakened" in trend.lower()
    finally:
        db.close()


def test_duplicate_risk_is_confirmed_not_cloned():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        cand = {
            "ticker": "NVDA",
            "memory_type": "RISK",
            "statement": "Export restrictions are a risk.",
            "theme_key": "export_restrictions",
            "observed_at": EVENT_TIME,
            "investigation_id": case.id,
            "source_type": "filing",
        }
        first, _ = company_memory.upsert_memory(db, owner_key=OWNER_A, candidate=cand)
        second, action = company_memory.upsert_memory(db, owner_key=OWNER_A, candidate=dict(cand))
        assert action == "DEDUPLICATED"
        assert first.id == second.id
        assert second.observation_count == 2
        assert second.status == "CONFIRMED"
    finally:
        db.close()


def test_status_updates_preserve_row():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        item, _ = company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions represent a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
            },
        )
        weakened = company_memory.apply_status(
            db, owner_key=OWNER_A, memory_id=item.id, status="WEAKENED"
        )
        assert weakened.status == "WEAKENED"
        archived = company_memory.apply_status(
            db, owner_key=OWNER_A, memory_id=item.id, status="ARCHIVED"
        )
        assert archived.statement.startswith("Export restrictions")
        assert archived.id == item.id
    finally:
        db.close()


def test_portfolio_language_is_never_stored():
    ok = company_memory._quality_ok(
        {
            "memory_type": "COMPANY_FACT",
            "statement": "NVDA represents 35% of this user's portfolio.",
            "observed_at": EVENT_TIME,
            "investigation_id": 1,
        }
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Retrieval / similarity / isolation
# ---------------------------------------------------------------------------


def test_retrieval_is_relevant_and_owner_scoped():
    db = SessionLocal()
    try:
        case_a = _create_case(db, OWNER_A)
        case_b = _create_case(db, OWNER_B)
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions are a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case_a.id,
            },
        )
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_B,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "User B private export note that must not leak.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case_b.id,
            },
        )
        ctx = company_memory.retrieve_for_investigation(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            hypotheses=["export restrictions"],
        )
        blob = " ".join(i.statement for i in ctx.items)
        assert "Export restrictions are a recurring" in blob
        assert "User B private" not in blob
        assert "portfolio" not in ctx.model_dump_json().lower()
    finally:
        db.close()


def test_similar_event_not_called_same_event():
    db = SessionLocal()
    try:
        first = _create_case(db, OWNER_A)
        evidence_ledger_store.update_move_metadata(
            db, owner_key=OWNER_A, investigation_id=first.id, move_pct=-9.2, window_label="1d"
        )
        evidence_ledger_store.mark_complete(
            db, owner_key=OWNER_A, investigation_id=first.id, summary="Prior regulatory drop."
        )
        second = _create_case(db, OWNER_A)
        similar = company_memory.find_similar_events(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            move_pct=-8.7,
            exclude_id=second.id,
        )
        assert similar
        assert similar[0].similarity == "similar"
        assert "not the same event" in similar[0].note.lower()
        tiny = company_memory.find_similar_events(
            db, owner_key=OWNER_A, ticker="NVDA", move_pct=-0.4, exclude_id=second.id
        )
        assert tiny == []
    finally:
        db.close()


def test_planner_receives_memory_without_portfolio():
    captured = {}

    async def fake_llm(**kwargs):
        captured["prompt"] = kwargs.get("user_prompt") or ""
        return {
            "focus": "Why NVDA moved",
            "tools": ["get_stock_news"],
            "seed_hypotheses": ["Guidance", "Sector"],
            "skip_reason": None,
            "need_browser_ir": False,
        }

    async def _run():
        with patch(
            "app.agents.investigation_planner_agent.call_openai_json",
            new=fake_llm,
        ):
            from app.agents import investigation_planner_agent

            return await investigation_planner_agent.plan_investigation(
                _move(),
                company_memory={
                    "summary": "Prior export-restriction investigations.",
                    "portfolio_exposure": 0.35,
                    "items": [{"statement": "Export restrictions are a recurring risk."}],
                },
            )

    plan = asyncio.get_event_loop().run_until_complete(_run())
    assert "company_memory" in captured["prompt"]
    assert "0.35" not in captured["prompt"]
    assert plan.get("company_memory", {}).get("portfolio_exposure") is None


def test_graph_load_memory_then_plan():
    investigation_graph.reset_investigation_graph()
    ranked = {
        "summary": "ok",
        "hypotheses": [
            {
                "statement": "Guidance concerns",
                "stance": "supports_move",
                "weight": 0.6,
                "confidence_score": 0.6,
                "evidence_indices": [0],
                "rank": 1,
            }
        ],
    }
    evidence = [
        {
            "source_type": "price",
            "title": "move",
            "excerpt": "down",
            "source_url": "",
            "raw_payload": {},
        },
        {
            "source_type": "news",
            "title": "A",
            "excerpt": "A",
            "source_url": "https://example.com/a",
            "raw_payload": {"tool": "get_stock_news"},
        },
        {
            "source_type": "news",
            "title": "B",
            "excerpt": "B",
            "source_url": "https://example.com/b",
            "raw_payload": {"tool": "get_stock_news"},
        },
        {
            "source_type": "filing",
            "title": "8-K",
            "excerpt": "Item 2.02",
            "source_url": "https://www.sec.gov/x",
            "raw_payload": {"tool": "get_recent_filings"},
        },
    ]

    async def _run():
        with patch(
            "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
            new=AsyncMock(
                return_value={
                    "focus": "NVDA",
                    "tools": ["get_stock_news"],
                    "seed_hypotheses": ["Guidance", "Macro"],
                    "skip_reason": None,
                    "need_browser_ir": False,
                }
            ),
        ) as planner, patch(
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
                    "counterargument": "Sector remains plausible.",
                    "leading_weakened": False,
                    "reversal": False,
                    "notes": [],
                    "confidence_delta": 0.0,
                }
            ),
        ):
            result = await investigation_graph.run_investigation_graph(
                _move(),
                ticker="NVDA",
                investigation_id=81,
                owner_key=OWNER_A,
                company_memory={
                    "summary": "Prior regulatory investigation.",
                    "items": [{"statement": "Export restrictions"}],
                },
                max_iterations=1,
            )
            return result, planner

    result, planner = asyncio.get_event_loop().run_until_complete(_run())
    assert result.status == "complete"
    assert planner.await_args.kwargs.get("company_memory") or planner.call_args.kwargs.get(
        "company_memory"
    )
    assert result.company_memory.get("summary")


def test_contradict_marks_status_without_deleting():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        item, _ = company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions represent a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
            },
        )
        contradicted = company_memory.apply_status(
            db, owner_key=OWNER_A, memory_id=item.id, status="CONTRADICTED"
        )
        assert contradicted.status == "CONTRADICTED"
        assert contradicted.statement.startswith("Export restrictions")
        assert contradicted.id == item.id
    finally:
        db.close()


def test_irrelevant_memory_is_ranked_below_relevant():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions are a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
            },
        )
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "COMPANY_FACT",
                "statement": "NVIDIA sells professional visualization GPUs.",
                "theme_key": "general",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
            },
        )
        ctx = company_memory.retrieve_for_investigation(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            hypotheses=["export restrictions", "regulatory"],
        )
        assert ctx.items
        assert ctx.items[0].theme_key == "export_restrictions"
        assert "Current evidence takes precedence" in ctx.summary
    finally:
        db.close()


def test_flagship_second_investigation_retrieves_prior_risk():
    db = SessionLocal()
    try:
        first = _create_case(db, OWNER_A)
        evidence_ledger_store.update_move_metadata(
            db, owner_key=OWNER_A, investigation_id=first.id, move_pct=-8.7, window_label="1d"
        )
        stored = company_memory.extract_and_store(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            investigation_id=first.id,
            hypotheses=[
                {
                    "statement": "Export restrictions are a recurring regulatory risk.",
                    "claim_type": "RISK",
                    "evidence_indices": [0],
                    "supporting_indices": [0],
                }
            ],
            evidence=[_ir()],
            move_pct=-8.7,
            occurred_at=datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        evidence_ledger_store.mark_complete(
            db, owner_key=OWNER_A, investigation_id=first.id, summary="Regulatory / export risk."
        )
        assert any(i.memory_type == "RISK" for i in stored)
        second = _create_case(db, OWNER_A)
        ctx = company_memory.retrieve_for_investigation(
            db,
            owner_key=OWNER_A,
            ticker="NVDA",
            event={"event_type": "PRICE_MOVE", "ticker": "NVDA"},
            hypotheses=["export restrictions"],
            move_pct=-9.1,
            investigation_id=second.id,
        )
        blob = " ".join(i.statement for i in ctx.items)
        assert "Export restrictions" in blob
        assert ctx.similar_events
        assert ctx.similar_events[0].similarity == "similar"
        assert "not the same event" in ctx.similar_events[0].note.lower()
        assert first.id in ctx.related_investigation_ids
        assert "Current evidence takes precedence" in ctx.summary
    finally:
        db.close()


def test_hypothesis_context_marks_memory_as_historical_not_current():
    note = company_memory.historical_vs_current_note()
    assert "not current-event evidence" in note
    assert "cannot support a new causal claim" in note
    ctx = investigation_graph._hypothesis_context(
        {
            "hypotheses": [],
            "evidence_items": [],
            "event": {"event_type": "PRICE_MOVE"},
            "move": _move(),
            "company_memory": {"summary": "Prior export-restriction investigations."},
        }
    )
    assert ctx["company_memory"]["summary"].startswith("Prior export")
    assert "not current-event evidence" in ctx["historical_memory_note"]


def test_memory_api_is_additive_and_isolated():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A)
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions are a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
            },
        )
    finally:
        db.close()
    mine = client.get("/api/investigations/memory/NVDA", headers=GUEST_A)
    assert mine.status_code == 200
    assert mine.json()["ticker"] == "NVDA"
    other = client.get(
        "/api/investigations/memory/NVDA",
        headers={"X-Guest-Id": "phase8ownerbbbbbbbbbbbbbbbbbb"},
    )
    assert other.status_code == 200
    assert "Export restrictions are a recurring" not in str(other.json().get("recurring_risks"))
    listed = client.get("/api/investigations", headers=GUEST_A)
    assert listed.status_code == 200

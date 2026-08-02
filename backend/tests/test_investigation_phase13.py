"""PRD v3 Phase 13 — roster pass + observability status."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase13.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["SENTRY_DSN"] = ""
os.environ["OTEL_ENABLED"] = "false"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.agent_schemas import (  # noqa: E402
    EarningsAgentOutput,
    InvestmentMemoOutput,
    MacroAgentOutput,
)
from app.models.investigation_schemas import InvestigationCreateRequest  # noqa: E402
from app.observability.instrumentation import get_observability_status  # noqa: E402
from app.services import evidence_ledger_store  # noqa: E402
from app.services.investigation_roster import run_roster_pass  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()
client = TestClient(app)


def test_health_includes_observability_flags():
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "langsmith" in body
    assert "sentry" in body
    assert "otel" in body
    assert body["langsmith"] is False
    assert body["sentry"] is False
    assert body["otel"] is False


def test_observability_status_endpoint():
    res = client.get("/api/observability/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert set(body.keys()) >= {"status", "langsmith", "sentry", "otel"}
    cached = get_observability_status()
    assert cached["langsmith"] == body["langsmith"]


def test_roster_pass_attaches_earnings_macro_memo():
    move = MoveSnapshot(
        ticker="MSFT",
        move_pct=-3.2,
        window_label="1d",
        current_price=400.0,
        previous_close=413.0,
        is_noise=False,
        source="test",
        detail="MSFT down",
    )
    evidence = [
        {
            "source_type": "news",
            "title": "Fed holds rates as inflation cools",
            "excerpt": "Macro headline",
            "source_url": "https://example.com/n",
            "raw_payload": {"title": "Fed holds rates as inflation cools", "publisher": "X"},
        },
        {
            "source_type": "filing",
            "title": "10-Q",
            "excerpt": "Quarterly report",
            "source_url": "",
            "raw_payload": {"form_type": "10-Q", "filing_date": "2026-01-15"},
        },
    ]
    earn = EarningsAgentOutput(
        earnings_summary="Stable margins; watch next print.",
        tone="mixed",
        key_points=["Margins stable"],
        next_catalyst="Next earnings",
        confidence_score=0.5,
    )
    mac = MacroAgentOutput(
        macro_summary="Rates theme in headlines.",
        relevance="medium",
        themes=["fed", "inflation"],
        portfolio_implications=["Watch yields"],
        confidence_score=0.5,
    )
    memo = InvestmentMemoOutput(
        ticker="MSFT",
        memo_title="MSFT Investment Memo",
        one_liner="Investigation brief for MSFT move.",
        investment_thesis="Thesis",
        key_numbers=["Price: 400"],
        catalysts=["Earnings"],
        risks=["Macro"],
        bull_case_summary="Bull",
        bear_case_summary="Bear",
        decision="WATCH",
        conviction="medium",
        time_horizon="3-12 months",
        what_would_change_my_mind=["Guidance cut"],
        disclaimer="Educational only.",
        confidence_score=0.5,
    )

    with patch(
        "app.services.investigation_roster.analyze_earnings",
        new=AsyncMock(return_value=(earn, {})),
    ), patch(
        "app.services.investigation_roster.analyze_macro",
        new=AsyncMock(return_value=mac),
    ), patch(
        "app.services.investigation_roster.generate_investment_memo",
        new=AsyncMock(return_value=memo),
    ):
        roster = asyncio.get_event_loop().run_until_complete(
            run_roster_pass(
                move,
                evidence,
                hypotheses=[{"statement": "Guidance cut fears", "rank": 1}],
                verification_notes="Grounded.",
                da_outcome={"counterargument": "Could be sector beta."},
            )
        )

    assert roster["earnings"]["earnings_summary"]
    assert roster["macro"]["relevance"] == "medium"
    assert "Investigation Brief" in roster["memo"]["memo_title"]


def test_roster_persisted_on_investigation_detail():
    db = SessionLocal()
    try:
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key="guest:phase13aaaaaaaaaaaaaaaaaaaa",
            user=None,
            body=InvestigationCreateRequest(
                ticker="MSFT",
                trigger_reason="on_demand",
                summary="seed",
            ),
        )
        assert created is not None
        evidence_ledger_store.set_roster_context(
            db,
            owner_key="guest:phase13aaaaaaaaaaaaaaaaaaaa",
            investigation_id=created.id,
            roster={
                "earnings": {"earnings_summary": "ok", "tone": "mixed"},
                "macro": {"macro_summary": "rates", "relevance": "low"},
                "memo": {
                    "memo_title": "MSFT Investigation Brief",
                    "one_liner": "Brief line",
                    "decision": "WATCH",
                },
            },
        )
        detail = evidence_ledger_store.get_investigation(
            db,
            owner_key="guest:phase13aaaaaaaaaaaaaaaaaaaa",
            investigation_id=created.id,
        )
        assert detail is not None
        assert detail.roster is not None
        assert detail.roster.memo.get("decision") == "WATCH"
    finally:
        db.close()

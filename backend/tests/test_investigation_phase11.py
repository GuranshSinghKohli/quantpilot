"""PRD v3 Phase 11 — scheduling, trigger logic, asset-class paths."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase11.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_NOISE_PCT"] = "1.5"
os.environ["INVESTIGATION_VOL_Z_THRESHOLD"] = "1.5"
os.environ["INVESTIGATION_IDIO_PCT"] = "1.0"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import investigation_planner_agent  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.jobs.investigation_sweep_job import run_investigation_sweep  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_schemas import InvestigationCreateRequest  # noqa: E402
from app.services import asset_class as asset_class_service  # noqa: E402
from app.services import evidence_ledger_store  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.services.trigger_logic import TriggerDecision, _decide  # noqa: E402

init_db()
client = TestClient(app)


def _auth_headers(email: str = "phase11@example.com") -> dict:
    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
    )
    if reg.status_code >= 400:
        reg = client.post(
            "/api/auth/login",
            json={"email": email, "password": "password123"},
        )
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def _move(
    pct: float,
    *,
    ticker: str = "NVDA",
    noise: bool = False,
) -> MoveSnapshot:
    return MoveSnapshot(
        ticker=ticker,
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=100.0 * (1 - pct / 100.0),
        is_noise=noise,
        source="test",
        detail=f"{ticker} moved {pct}%",
    )


def test_known_etf_classification():
    assert asset_class_service.classify_asset("SPY") == "etf"
    assert asset_class_service.classify_asset("QQQ") == "etf"


def test_decide_skips_market_beta_residual():
    move = _move(-2.0)
    decision = _decide(
        move=move,
        asset_class="equity",
        realized_vol_pct=1.5,
        move_zscore=-1.3,
        benchmark_move_pct=-1.8,
        residual_pct=-0.2,
        benchmark="SPY",
    )
    assert decision.should_investigate is False
    assert "residual" in decision.reason.lower() or "SPY" in decision.reason


def test_decide_skips_low_vol_zscore():
    move = _move(-2.0)
    decision = _decide(
        move=move,
        asset_class="equity",
        realized_vol_pct=2.0,
        move_zscore=-0.8,
        benchmark_move_pct=0.1,
        residual_pct=-2.1,
        benchmark="SPY",
    )
    assert decision.should_investigate is False
    assert "z-score" in decision.reason.lower() or "vol" in decision.reason.lower()


def test_decide_fires_on_idiosyncratic_downside():
    move = _move(-4.5)
    decision = _decide(
        move=move,
        asset_class="equity",
        realized_vol_pct=1.5,
        move_zscore=-3.0,
        benchmark_move_pct=-0.2,
        residual_pct=-4.3,
        benchmark="SPY",
    )
    assert decision.should_investigate is True
    assert decision.depth in {"standard", "deep"}


def test_etf_planner_skips_filings_and_ir():
    move = _move(-3.2, ticker="XLK")
    move.asset_class = "etf"
    move.depth = "standard"
    fallback = investigation_planner_agent._fallback_plan(move, asset_hint="etf")
    assert "get_recent_filings" not in fallback["tools"]
    assert "get_ir_materials" not in fallback["tools"]
    assert "get_stock_news" in fallback["tools"]
    assert "get_price_history" in fallback["tools"]

    with patch(
        "app.agents.investigation_planner_agent.call_openai_json",
        new=AsyncMock(return_value=fallback),
    ):
        plan = asyncio.get_event_loop().run_until_complete(
            investigation_planner_agent.plan_investigation(move, asset_hint="etf")
        )
    assert "get_recent_filings" not in plan["tools"]
    assert "get_ir_materials" not in plan["tools"]


def test_cooldown_helper():
    db = SessionLocal()
    try:
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key="user:99991",
            user=None,
            body=InvestigationCreateRequest(
                ticker="AAPL",
                trigger_reason="scheduled",
                summary="cooldown test",
            ),
        )
        assert created is not None
        assert evidence_ledger_store.recent_investigation_exists(
            db, owner_key="user:99991", ticker="AAPL", within_hours=6
        )
        assert not evidence_ledger_store.recent_investigation_exists(
            db, owner_key="user:99991", ticker="MSFT", within_hours=6
        )
    finally:
        db.close()


def test_sweep_dry_run_and_preview_endpoints():
    headers = _auth_headers("phase11sweep@example.com")
    client.post("/api/watchlist/NVDA", headers=headers)

    decision = TriggerDecision(
        should_investigate=True,
        move=_move(-5.0),
        reason="test fire",
        depth="deep",
        asset_class="equity",
        realized_vol_pct=1.8,
        move_zscore=-2.8,
        benchmark_move_pct=-0.3,
        residual_pct=-4.7,
    )

    with patch(
        "app.jobs.investigation_sweep_job.trigger_logic.evaluate_trigger",
        new=AsyncMock(return_value=decision),
    ):
        res = client.post(
            "/api/investigations/sweep",
            json={"dry_run": True, "max_launches": 5},
            headers=headers,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dry_run"] is True
    assert body["evaluated"] >= 1
    assert body["launched"] >= 1
    assert any(d.get("action") == "would_launch" for d in body["details"])

    with patch(
        "app.routers.investigations.trigger_logic.evaluate_trigger",
        new=AsyncMock(return_value=decision),
    ):
        preview = client.get("/api/investigations/trigger/NVDA", headers=headers)
    assert preview.status_code == 200, preview.text
    pdata = preview.json()
    assert pdata["should_investigate"] is True
    assert pdata["ticker"] == "NVDA"
    assert pdata["depth"] == "deep"


def test_sweep_requires_auth():
    res = client.post("/api/investigations/sweep", json={"dry_run": True})
    assert res.status_code == 401


def test_run_investigation_sweep_respects_cooldown():
    headers = _auth_headers("phase11cool@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    user_id = me["id"]
    client.post("/api/watchlist/TSLA", headers=headers)

    db = SessionLocal()
    try:
        evidence_ledger_store.create_investigation(
            db,
            owner_key=f"user:{user_id}",
            user=None,
            body=InvestigationCreateRequest(
                ticker="TSLA",
                trigger_reason="scheduled",
                summary="recent",
            ),
        )
        user = MagicMock()
        user.id = user_id
        decision = TriggerDecision(
            should_investigate=True,
            move=_move(-6.0, ticker="TSLA"),
            reason="would fire",
            depth="deep",
            asset_class="equity",
            realized_vol_pct=2.0,
            move_zscore=-3.0,
            benchmark_move_pct=0.0,
            residual_pct=-6.0,
        )
        with patch(
            "app.jobs.investigation_sweep_job.trigger_logic.evaluate_trigger",
            new=AsyncMock(return_value=decision),
        ), patch(
            "app.jobs.investigation_sweep_job.watchlist_store.list_watchlist",
            return_value=[MagicMock(ticker="TSLA")],
        ):
            result = asyncio.get_event_loop().run_until_complete(
                run_investigation_sweep(user=user, dry_run=True, max_launches=5)
            )
        assert result["skipped_cooldown"] >= 1
        assert result["launched"] == 0
    finally:
        db.close()

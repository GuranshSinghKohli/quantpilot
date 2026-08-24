"""Phase 2 — event detection, investigation materiality, and gate."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase2.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["REDIS_URL"] = ""
os.environ["INVESTIGATION_MATERIALITY_THRESHOLD"] = "40"
os.environ["INVESTIGATION_EXPOSURE_WEIGHT"] = "100"
os.environ["INVESTIGATION_IDIO_PCT"] = "1.0"
os.environ["INVESTIGATION_NOTIFY_ENABLED"] = "true"
os.environ["INVESTIGATION_NOTIFY_MIN_SCORE"] = "40"
os.environ["INVESTIGATION_NOTIFY_MIN_Z"] = "2.0"
os.environ["INVESTIGATION_NOTIFY_MIN_DEPTH"] = "standard"

import asyncio  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models.event_schemas import (  # noqa: E402
    EventDirection,
    EventSeverity,
    EventType,
    MarketEvent,
)
from app.models.investigation_schemas import (  # noqa: E402
    InvestigationCreateRequest,
    InvestigationDetail,
)
from app.services import (  # noqa: E402
    event_detection,
    evidence_ledger_store,
    investigation_gate,
    investigation_notifications,
    materiality,
    redis_store,
)
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.services.portfolio_exposure import event_impact, portfolio_weight  # noqa: E402
from app.services.trigger_logic import TriggerDecision, _decide  # noqa: E402

init_db()


def _move(pct: float, ticker: str = "NVDA") -> MoveSnapshot:
    return MoveSnapshot(
        ticker=ticker,
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=100.0 * (1 - pct / 100.0),
        is_noise=abs(pct) < 1.5,
        source="test",
        detail=f"{ticker} moved {pct}%",
        benchmark_ticker="SPY",
    )


def _decision(
    pct: float,
    *,
    residual: float,
    z: float,
    bench: float,
    should: bool = True,
    depth: str = "deep",
) -> TriggerDecision:
    move = _move(pct)
    return TriggerDecision(
        should_investigate=should,
        move=move,
        reason="test",
        depth=depth,
        asset_class="equity",
        realized_vol_pct=2.0,
        move_zscore=z,
        benchmark_move_pct=bench,
        residual_pct=residual,
    )


def _event(**kwargs) -> MarketEvent:
    base = dict(
        event_id="test-event",
        ticker="NVDA",
        event_type=EventType.PRICE_MOVE,
        direction=EventDirection.DOWN,
        magnitude=-8.0,
        benchmark="SPY",
        z_score=-4.0,
        residual_pct=-7.0,
        portfolio_exposure=0.22,
        severity=EventSeverity.HIGH,
        source="test",
        reason="test",
    )
    base.update(kwargs)
    return MarketEvent(**base)


def test_decide_still_skips_normal_and_market_wide_moves():
    quiet = _decide(
        move=_move(-1.2),
        asset_class="equity",
        realized_vol_pct=2.0,
        move_zscore=-0.6,
        benchmark_move_pct=-1.0,
        residual_pct=-0.2,
        benchmark="SPY",
    )
    assert quiet.should_investigate is False

    idio = _decide(
        move=_move(-8.0),
        asset_class="equity",
        realized_vol_pct=2.0,
        move_zscore=-4.0,
        benchmark_move_pct=-1.0,
        residual_pct=-7.0,
        benchmark="SPY",
    )
    assert idio.should_investigate is True
    assert idio.depth in {"standard", "deep"}


def test_events_from_trigger_large_idiosyncratic_and_vol():
    events = event_detection.events_from_trigger(
        _decision(-8.0, residual=-7.0, z=-4.0, bench=-1.0)
    )
    types = {e.event_type for e in events}
    assert EventType.PRICE_MOVE in types
    assert EventType.IDIOSYNCRATIC_MOVE in types
    assert EventType.VOLATILITY_SPIKE in types
    price = next(e for e in events if e.event_type == EventType.PRICE_MOVE)
    assert price.direction == EventDirection.DOWN
    assert price.magnitude == -8.0
    assert price.residual_pct == -7.0


def test_events_from_trigger_low_vol_small_move():
    events = event_detection.events_from_trigger(
        _decision(-1.0, residual=-0.2, z=-0.4, bench=-0.8, should=False, depth="light")
    )
    types = {e.event_type for e in events}
    assert EventType.PRICE_MOVE in types
    assert EventType.IDIOSYNCRATIC_MOVE not in types
    assert EventType.VOLATILITY_SPIKE not in types


def test_filing_event_from_recent_8k():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    payload = {
        "filings": [
            {
                "form_type": "8-K",
                "filing_date": "2026-08-22",
                "accession_number": "0001",
                "document_url": "https://www.sec.gov/example",
            }
        ]
    }
    events = event_detection.events_from_filings("NVDA", payload, now=now)
    assert len(events) == 1
    assert events[0].event_type == EventType.FILING
    assert events[0].metadata["filing_type"] == "8-K"
    assert events[0].severity == EventSeverity.HIGH


def test_filing_ignores_stale_and_immaterial_forms():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    payload = {
        "filings": [
            {"form_type": "4", "filing_date": "2026-08-22"},
            {"form_type": "10-Q", "filing_date": "2026-07-01"},
        ]
    }
    assert event_detection.events_from_filings("NVDA", payload, now=now) == []


def test_earnings_event_in_window():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    events = event_detection.events_from_fundamentals(
        "NVDA",
        {"earnings_date": "2026-08-24", "earnings_surprise_pct": 4.2},
        now=now,
    )
    assert len(events) == 1
    assert events[0].event_type == EventType.EARNINGS
    assert events[0].metadata["upcoming"] is True


def test_earnings_outside_window_ignored():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    events = event_detection.events_from_fundamentals(
        "NVDA",
        {"earnings_date": "2026-09-30"},
        now=now,
    )
    assert events == []


def test_portfolio_exposure_low_and_high():
    holdings = [
        SimpleNamespace(ticker="NVDA", shares=220.0, avg_cost=100.0),
        SimpleNamespace(ticker="AAPL", shares=780.0, avg_cost=100.0),
    ]
    assert abs(portfolio_weight("NVDA", holdings) - 0.22) < 0.001
    tiny = [
        SimpleNamespace(ticker="NVDA", shares=2.0, avg_cost=100.0),
        SimpleNamespace(ticker="AAPL", shares=98.0, avg_cost=100.0),
    ]
    assert abs(portfolio_weight("NVDA", tiny) - 0.02) < 0.001
    assert portfolio_weight("NVDA", [SimpleNamespace(ticker="NVDA", shares=None)]) == 0.0
    assert event_impact(-8.0, 0.22) == 1.76


def test_investigation_materiality_example_idio_high_exposure():
    scored = materiality.assess_investigation_materiality(
        _event(
            event_type=EventType.IDIOSYNCRATIC_MOVE,
            magnitude=-8.0,
            residual_pct=-7.0,
            z_score=-4.0,
            portfolio_exposure=0.22,
        )
    )
    assert scored.should_investigate is True
    assert scored.score >= 40
    assert scored.depth == "deep"
    assert "Investigating NVDA" in scored.reason
    assert "exposure=22.0%" in scored.reason


def test_investigation_materiality_example_market_wide_low_exposure():
    scored = materiality.assess_investigation_materiality(
        _event(
            event_type=EventType.PRICE_MOVE,
            magnitude=-1.2,
            residual_pct=-0.2,
            z_score=-0.6,
            portfolio_exposure=0.02,
            severity=EventSeverity.LOW,
        )
    )
    assert scored.should_investigate is False
    assert "tracks the benchmark" in scored.reason or "Skipped" in scored.reason


def test_investigation_materiality_8k_filing():
    scored = materiality.assess_investigation_materiality(
        _event(
            event_id="nvda-8k",
            event_type=EventType.FILING,
            direction=EventDirection.NONE,
            magnitude=None,
            z_score=None,
            residual_pct=None,
            portfolio_exposure=0.0,
            metadata={"filing_type": "8-K"},
            reason="NVDA filed 8-K",
        )
    )
    assert scored.should_investigate is True
    assert scored.primary_event_type == "FILING"
    assert scored.score >= 40


def test_investigation_materiality_below_and_borderline():
    below = materiality.assess_investigation_materiality(
        _event(
            event_type=EventType.PRICE_MOVE,
            magnitude=-2.6,
            residual_pct=-1.2,
            z_score=-1.2,
            portfolio_exposure=0.0,
            severity=EventSeverity.LOW,
        ),
        threshold=40,
    )
    assert below.should_investigate is False
    assert below.score < 40

    above = materiality.assess_investigation_materiality(
        _event(
            event_type=EventType.PRICE_MOVE,
            magnitude=-3.5,
            residual_pct=-3.2,
            z_score=-2.2,
            portfolio_exposure=0.05,
        ),
        threshold=40,
    )
    assert above.should_investigate is True


def test_event_fingerprint_cooldown_and_expiry():
    event = _event(event_type=EventType.PRICE_MOVE)
    owner = "user:phase2"
    redis_store.cache_set("warmup", 1, ttl_seconds=1)
    assert investigation_gate.fingerprint_seen(owner, event) is False
    investigation_gate.remember_fingerprint(owner, event)
    assert investigation_gate.fingerprint_seen(owner, event) is True
    other = _event(event_id="up", direction=EventDirection.UP, magnitude=8.0)
    assert investigation_gate.fingerprint_seen(owner, other) is False


def test_gate_passes_material_event_and_skips_immaterial(monkeypatch):
    material = _decision(-8.0, residual=-7.0, z=-4.0, bench=-1.0)
    events = event_detection.events_from_trigger(material)
    monkeypatch.setattr(
        investigation_gate,
        "evaluate_trigger",
        AsyncMock(return_value=material),
    )
    monkeypatch.setattr(
        investigation_gate,
        "detect_events",
        AsyncMock(return_value=events),
    )
    holdings = [
        SimpleNamespace(ticker="NVDA", shares=22.0, avg_cost=100.0),
        SimpleNamespace(ticker="MSFT", shares=78.0, avg_cost=100.0),
    ]
    passed = asyncio.get_event_loop().run_until_complete(
        investigation_gate.evaluate_investigation_gate(
            "NVDA",
            holdings=holdings,
            persist_fingerprint=False,
        )
    )
    assert passed.should_investigate is True
    assert passed.score >= 40
    assert passed.exposure > 0.2

    quiet = _decision(-1.2, residual=-0.2, z=-0.5, bench=-1.0, should=False, depth="light")
    monkeypatch.setattr(
        investigation_gate,
        "evaluate_trigger",
        AsyncMock(return_value=quiet),
    )
    monkeypatch.setattr(
        investigation_gate,
        "detect_events",
        AsyncMock(return_value=event_detection.events_from_trigger(quiet)),
    )
    skipped = asyncio.get_event_loop().run_until_complete(
        investigation_gate.evaluate_investigation_gate(
            "NVDA",
            holdings=[SimpleNamespace(ticker="NVDA", shares=2.0, avg_cost=100.0)],
        )
    )
    assert skipped.should_investigate is False


def test_gate_investigates_8k_without_price_move(monkeypatch):
    flat = _decision(0.2, residual=0.1, z=0.1, bench=0.1, should=False, depth="light")
    filing = event_detection.events_from_filings(
        "NVDA",
        {"filings": [{"form_type": "8-K", "filing_date": datetime.now(timezone.utc).date().isoformat()}]},
        now=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(investigation_gate, "evaluate_trigger", AsyncMock(return_value=flat))
    monkeypatch.setattr(investigation_gate, "detect_events", AsyncMock(return_value=filing))
    gate = asyncio.get_event_loop().run_until_complete(
        investigation_gate.evaluate_investigation_gate("NVDA")
    )
    assert gate.should_investigate is True
    assert gate.primary_event is not None
    assert gate.primary_event.event_type == EventType.FILING


def test_gate_ledger_cooldown(monkeypatch):
    db = SessionLocal()
    try:
        evidence_ledger_store.create_investigation(
            db,
            owner_key="user:phase2cool",
            user=None,
            body=InvestigationCreateRequest(ticker="NVDA", trigger_reason="scheduled"),
        )
        material = _decision(-8.0, residual=-7.0, z=-4.0, bench=-1.0)
        monkeypatch.setattr(
            investigation_gate, "evaluate_trigger", AsyncMock(return_value=material)
        )
        monkeypatch.setattr(
            investigation_gate,
            "detect_events",
            AsyncMock(return_value=event_detection.events_from_trigger(material)),
        )
        gate = asyncio.get_event_loop().run_until_complete(
            investigation_gate.evaluate_investigation_gate(
                "NVDA",
                owner_key="user:phase2cool",
                db=db,
            )
        )
        assert gate.should_investigate is False
        assert gate.cooldown is True
    finally:
        db.close()


def test_notification_materiality_still_separate():
    move = _move(-5.5)
    move.move_zscore = -3.4
    move.residual_pct = -5.0
    move.depth = "deep"
    notify = materiality.assess_materiality(move)
    assert notify.should_notify is True

    light = _move(-1.8)
    light.move_zscore = -1.1
    light.residual_pct = -0.9
    light.depth = "light"
    assert materiality.assess_materiality(light).should_notify is False


def test_on_demand_notification_still_suppressed():
    db = SessionLocal()
    try:
        user = SimpleNamespace(id=1, email="phase2notify@example.com")
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key="user:1",
            user=None,
            body=InvestigationCreateRequest(ticker="AAPL", trigger_reason="on_demand"),
        )
        assert created is not None
        move = _move(-6.0, ticker="AAPL")
        move.move_zscore = -3.0
        move.residual_pct = -5.0
        move.depth = "deep"
        event = investigation_notifications.maybe_notify_investigation(
            db,
            user=user,
            detail=InvestigationDetail(
                id=created.id,
                ticker="AAPL",
                trigger_reason="on_demand",
                status="complete",
                move_pct=-6.0,
                window_label="1d",
                summary="User asked.",
                created_at=created.created_at,
                claims_count=0,
                evidence_count=0,
                claims=[],
                evidence_items=[],
            ),
            move=move,
        )
        assert event is None
    finally:
        db.close()

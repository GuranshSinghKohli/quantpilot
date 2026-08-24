"""Phase 9 — autonomous monitoring, reopen, and intelligent notify."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase9_monitor.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"

import asyncio  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.models.event_schemas import EventType, MarketEvent  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    ClaimOut,
    InvestigationCreateRequest,
    InvestigationDetail,
)
from app.services import (  # noqa: E402
    autonomous_monitor,
    evidence_ledger_store,
    investigation_gate,
    investigation_notifications,
)
from app.services.move_detector import MoveSnapshot  # noqa: E402
from app.jobs.investigation_sweep_job import run_investigation_sweep  # noqa: E402

init_db()
OWNER = "user:9009"
EVENT_TIME = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)


def _move(pct: float = -8.7, z: float = -4.0, residual: float = -7.5) -> MoveSnapshot:
    return MoveSnapshot(
        ticker="NVDA",
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=100.0 * (1 - pct / 100.0),
        is_noise=False,
        source="test",
        detail=f"NVDA moved {pct}%",
        residual_pct=residual,
        move_zscore=z,
        depth="deep",
    )


def _event(kind: EventType, mag: float = -8.7) -> MarketEvent:
    return MarketEvent(
        event_id=f"{kind.value}-{mag}",
        ticker="NVDA",
        event_type=kind,
        magnitude=mag,
        residual_pct=mag + 1.0,
        z_score=-4.0,
        reason="test event",
    )


def _complete_case(db, *, move_pct: float = -8.7, event_type: str = "PRICE_MOVE"):
    case = evidence_ledger_store.create_investigation(
        db,
        owner_key=OWNER,
        user=None,
        body=InvestigationCreateRequest(
            ticker="NVDA",
            trigger_reason="scheduled",
            summary="Prior regulatory drop.",
        ),
    )
    evidence_ledger_store.update_move_metadata(
        db, owner_key=OWNER, investigation_id=case.id, move_pct=move_pct, window_label="1d"
    )
    evidence_ledger_store.set_roster_context(
        db,
        owner_key=OWNER,
        investigation_id=case.id,
        roster={"investigation_graph": {"event": {"event_type": event_type}}},
    )
    evidence_ledger_store.mark_complete(
        db, owner_key=OWNER, investigation_id=case.id, summary="Prior case complete."
    )
    return evidence_ledger_store.latest_investigation(
        db, owner_key=OWNER, ticker="NVDA"
    )


def test_reopen_on_new_event_type():
    db = SessionLocal()
    try:
        last = _complete_case(db, event_type="PRICE_MOVE")
        decision = autonomous_monitor.decide_reopen(
            last=last,
            current_event=_event(EventType.FILING, -1.0),
            current_move_pct=-1.0,
        )
        assert decision.should_reopen is True
        assert decision.change_kind == "event_type"
        assert "FILING" in decision.reason
    finally:
        db.close()


def test_no_reopen_on_similar_price_move():
    db = SessionLocal()
    try:
        last = _complete_case(db, move_pct=-8.7, event_type="IDIOSYNCRATIC_MOVE")
        decision = autonomous_monitor.decide_reopen(
            last=last,
            current_event=_event(EventType.PRICE_MOVE, -8.5),
            current_move_pct=-8.5,
        )
        assert decision.should_reopen is False
        assert "no material change" in decision.reason.lower()
    finally:
        db.close()


def test_reopen_when_move_enlarges():
    db = SessionLocal()
    try:
        last = _complete_case(db, move_pct=-5.0, event_type="PRICE_MOVE")
        decision = autonomous_monitor.decide_reopen(
            last=last,
            current_event=_event(EventType.PRICE_MOVE, -9.2),
            current_move_pct=-9.2,
        )
        assert decision.should_reopen is True
        assert decision.change_kind == "magnitude"
    finally:
        db.close()


def test_in_flight_investigation_is_not_reopened():
    db = SessionLocal()
    try:
        case = evidence_ledger_store.create_investigation(
            db,
            owner_key=OWNER,
            user=None,
            body=InvestigationCreateRequest(ticker="NVDA", trigger_reason="scheduled"),
        )
        last = evidence_ledger_store.latest_investigation(
            db, owner_key=OWNER, ticker="NVDA"
        )
        decision = autonomous_monitor.decide_reopen(
            last=last,
            current_event=_event(EventType.FILING),
        )
        assert decision.should_reopen is False
        assert decision.in_flight is True
        assert case.id == last.id
    finally:
        db.close()


def test_memory_conflict_reopens():
    db = SessionLocal()
    try:
        last = _complete_case(db)
        decision = autonomous_monitor.decide_reopen(
            last=last,
            current_event=_event(EventType.PRICE_MOVE, -8.8),
            current_move_pct=-8.8,
            memory_changed=True,
        )
        assert decision.should_reopen is True
        assert decision.change_kind == "memory"
    finally:
        db.close()


def test_notify_requires_material_complete_claims():
    move = _move()
    claim = ClaimOut(
        id=1,
        statement="Export restrictions are the leading explanation.",
        stance="supports_move",
        confidence_score=0.7,
        rank=1,
        devil_advocate_notes="",
        evidence_links=[],
        created_at=EVENT_TIME,
    )
    ok = autonomous_monitor.decide_notify(
        status="complete",
        trigger_reason="scheduled",
        claims=[claim],
        move=move,
    )
    assert ok.should_notify is True
    empty = autonomous_monitor.decide_notify(
        status="complete",
        trigger_reason="scheduled",
        claims=[],
        move=move,
    )
    assert empty.should_notify is False
    noise = autonomous_monitor.decide_notify(
        status="complete",
        trigger_reason="scheduled",
        claims=[claim.model_copy(update={"stance": "market_noise"})],
        move=move,
    )
    assert noise.should_notify is False
    demand = autonomous_monitor.decide_notify(
        status="complete",
        trigger_reason="on_demand",
        claims=[claim],
        move=move,
    )
    assert demand.should_notify is False


def test_notify_suppressed_for_light_move():
    light = _move(pct=-1.2, z=-0.4, residual=-0.3)
    light.depth = "light"
    claim = SimpleNamespace(statement="beta", stance="supports_move", rank=1)
    decision = autonomous_monitor.decide_notify(
        status="complete",
        trigger_reason="scheduled",
        claims=[claim],
        move=light,
    )
    assert decision.should_notify is False


def test_gate_reopens_completed_case_for_filing(monkeypatch):
    db = SessionLocal()
    try:
        last = _complete_case(db, event_type="PRICE_MOVE")
        from app.services.trigger_logic import TriggerDecision

        trigger = TriggerDecision(
            should_investigate=False,
            move=_move(-1.0),
            reason="flat",
            depth="light",
            asset_class="equity",
            realized_vol_pct=1.0,
            move_zscore=-0.2,
            benchmark_move_pct=-0.8,
            residual_pct=-0.2,
        )
        filing = MarketEvent(
            event_id="8k",
            ticker="NVDA",
            event_type=EventType.FILING,
            magnitude=-1.0,
            metadata={"filing_type": "8-K"},
            reason="8-K",
        )
        monkeypatch.setattr(
            investigation_gate, "evaluate_trigger", AsyncMock(return_value=trigger)
        )
        monkeypatch.setattr(
            investigation_gate, "detect_events", AsyncMock(return_value=[filing])
        )
        gate = asyncio.get_event_loop().run_until_complete(
            investigation_gate.evaluate_investigation_gate(
                "NVDA", owner_key=OWNER, db=db
            )
        )
        assert gate.reopen is True
        assert gate.should_investigate is True
        assert gate.prior_investigation_id == last.id
        assert gate.cooldown is False
    finally:
        db.close()


def test_sweep_skips_in_flight_without_calling_gate(monkeypatch):
    db = SessionLocal()
    try:
        evidence_ledger_store.create_investigation(
            db,
            owner_key="user:9010",
            user=None,
            body=InvestigationCreateRequest(ticker="TSLA", trigger_reason="scheduled"),
        )
    finally:
        db.close()
    user = MagicMock()
    user.id = 9010
    called = {"n": 0}

    async def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("gate should not run for in-flight cases")

    monkeypatch.setattr(
        "app.jobs.investigation_sweep_job.evaluate_investigation_gate", _boom
    )
    monkeypatch.setattr(
        "app.jobs.investigation_sweep_job.watchlist_store.list_watchlist",
        lambda *_a, **_k: [SimpleNamespace(ticker="TSLA")],
    )
    result = asyncio.get_event_loop().run_until_complete(
        run_investigation_sweep(user=user, dry_run=True, max_launches=5)
    )
    assert result["skipped_cooldown"] >= 1
    assert result["launched"] == 0
    assert called["n"] == 0


def test_maybe_notify_uses_decision_and_stays_owner_scoped():
    db = SessionLocal()
    try:
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key="user:9011",
            user=None,
            body=InvestigationCreateRequest(ticker="NVDA", trigger_reason="scheduled"),
        )
        user = MagicMock()
        user.id = 9011
        user.email = "phase9mon@example.com"
        detail = InvestigationDetail(
            id=created.id,
            ticker="NVDA",
            trigger_reason="scheduled",
            status="complete",
            move_pct=-8.7,
            window_label="1d",
            summary="ok",
            created_at=EVENT_TIME,
            completed_at=EVENT_TIME,
            claims_count=1,
            evidence_count=1,
            claims=[
                ClaimOut(
                    id=1,
                    statement="Guidance cut is the leading explanation.",
                    stance="supports_move",
                    confidence_score=0.6,
                    rank=1,
                    devil_advocate_notes="",
                    evidence_links=[],
                    created_at=EVENT_TIME,
                )
            ],
        )
        event = investigation_notifications.maybe_notify_investigation(
            db, user=user, detail=detail, move=_move()
        )
        assert event is not None
        assert event.alert_type == "investigation_material"
        silent = investigation_notifications.maybe_notify_investigation(
            db, user=user, detail=detail, move=_move()
        )
        assert silent is None
    finally:
        db.close()

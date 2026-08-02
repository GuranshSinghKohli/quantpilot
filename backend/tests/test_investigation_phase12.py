"""PRD v3 Phase 12 — proactive monitoring, materiality notify, Redis jobs."""

import os
from pathlib import Path
from unittest.mock import MagicMock

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase12.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["REDIS_URL"] = ""
os.environ["INVESTIGATION_NOTIFY_ENABLED"] = "true"
os.environ["INVESTIGATION_NOTIFY_MIN_SCORE"] = "40"
os.environ["INVESTIGATION_NOTIFY_MIN_Z"] = "2.0"
os.environ["INVESTIGATION_NOTIFY_MIN_DEPTH"] = "standard"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.jobs.investigation_sweep_job import (  # noqa: E402
    enqueue_investigation_sweep,
    process_investigation_queue,
)
from app.main import app  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    ClaimOut,
    InvestigationDetail,
)
from app.services import investigation_notifications, redis_store  # noqa: E402
from app.services.materiality import assess_materiality  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

# Reload materiality thresholds after env set (module may cache at import).
import app.services.materiality as materiality_mod  # noqa: E402

materiality_mod.NOTIFY_MIN_SCORE = 40.0
materiality_mod.NOTIFY_MIN_Z = 2.0
materiality_mod.NOTIFY_MIN_DEPTH = "standard"

init_db()
client = TestClient(app)


def _auth_headers(email: str = "phase12@example.com") -> dict:
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


def _move(**kwargs) -> MoveSnapshot:
    base = dict(
        ticker="NVDA",
        move_pct=-5.5,
        window_label="1d",
        current_price=100.0,
        previous_close=105.8,
        is_noise=False,
        source="test",
        detail="NVDA down",
        asset_class="equity",
        realized_vol_pct=1.6,
        move_zscore=-3.4,
        residual_pct=-5.0,
        depth="deep",
        should_investigate=True,
    )
    base.update(kwargs)
    return MoveSnapshot(**base)


def test_materiality_notifies_deep_idiosyncratic_move():
    assessment = assess_materiality(_move())
    assert assessment.score >= 40
    assert assessment.should_notify is True


def test_materiality_suppresses_light_betaish_move():
    assessment = assess_materiality(
        _move(
            move_pct=-1.8,
            move_zscore=-1.1,
            residual_pct=-0.9,
            depth="light",
        )
    )
    assert assessment.should_notify is False


def test_notify_creates_alert_event_with_investigation_link():
    headers = _auth_headers("phase12notify@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    db = SessionLocal()
    try:
        user = MagicMock()
        user.id = me["id"]
        user.email = me["email"]
        detail = InvestigationDetail(
            id=4242,
            ticker="NVDA",
            trigger_reason="scheduled",
            status="complete",
            move_pct=-5.5,
            window_label="1d",
            summary="Leading explanation is export-related.",
            created_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
            claims_count=1,
            evidence_count=2,
            error_message="",
            claims=[
                ClaimOut(
                    id=1,
                    statement="Export curb fears drove the drop",
                    stance="supports_move",
                    confidence_score=0.7,
                    rank=1,
                    devil_advocate_notes="",
                    evidence_links=[],
                    created_at="2026-01-01T00:00:00+00:00",
                )
            ],
            evidence_items=[],
        )
        # investigation_id FK may fail if id 4242 missing — insert without FK check
        # by using a real investigation row first.
        from app.models.investigation_schemas import InvestigationCreateRequest
        from app.services import evidence_ledger_store

        created = evidence_ledger_store.create_investigation(
            db,
            owner_key=f"user:{me['id']}",
            user=None,
            body=InvestigationCreateRequest(
                ticker="NVDA",
                trigger_reason="scheduled",
                summary="seed",
            ),
        )
        assert created is not None
        detail = detail.model_copy(update={"id": created.id})

        event = investigation_notifications.maybe_notify_investigation(
            db, user=user, detail=detail, move=_move()
        )
        assert event is not None
        assert event.alert_type == "investigation_material"
        assert event.investigation_id == created.id
        assert event.materiality_score is not None

        events = client.get("/api/alerts/events", headers=headers)
        assert events.status_code == 200
        body = events.json()
        assert any(e.get("investigation_id") == created.id for e in body)

        # Cooldown suppresses duplicate.
        again = investigation_notifications.maybe_notify_investigation(
            db, user=user, detail=detail, move=_move()
        )
        assert again is None
    finally:
        db.close()


def test_on_demand_does_not_notify():
    headers = _auth_headers("phase12demand@example.com")
    me = client.get("/api/auth/me", headers=headers).json()
    db = SessionLocal()
    try:
        user = MagicMock()
        user.id = me["id"]
        user.email = me["email"]
        detail = InvestigationDetail(
            id=1,
            ticker="AAPL",
            trigger_reason="on_demand",
            status="complete",
            move_pct=-6.0,
            window_label="1d",
            summary="User asked.",
            created_at="2026-01-01T00:00:00+00:00",
            claims_count=0,
            evidence_count=0,
            error_message="",
            claims=[],
            evidence_items=[],
        )
        event = investigation_notifications.maybe_notify_investigation(
            db, user=user, detail=detail, move=_move(ticker="AAPL")
        )
        assert event is None
    finally:
        db.close()


def test_investigation_queue_enqueue_drain():
    enqueue_investigation_sweep(dry_run=True, max_launches=1)
    # Drain with dry_run — no users required; should process one job.
    import asyncio

    processed = asyncio.get_event_loop().run_until_complete(
        process_investigation_queue(max_jobs=3)
    )
    assert processed >= 1
    assert redis_store.redis_mode() == "memory"

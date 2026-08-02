"""Per-owner analysis history isolation (user vs guest device)."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_analysis_history.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import analysis_history_store  # noqa: E402

init_db()
client = TestClient(app)


def _auth_headers(email: str = "hist@example.com") -> dict:
    password = "securepass1"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    if reg.status_code == 409:
        reg = client.post("/api/auth/login", json={"email": email, "password": password})
    assert reg.status_code in (200, 201), reg.text
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def test_guest_history_is_device_scoped():
    db = SessionLocal()
    try:
        analysis_history_store.add_run(
            db,
            owner_key="guest:device_aaa11111",
            user=None,
            ticker="NVDA",
            recommendation="HOLD",
            risk_level="MEDIUM",
        )
        analysis_history_store.add_run(
            db,
            owner_key="guest:device_bbb22222",
            user=None,
            ticker="AAPL",
            recommendation="BUY",
            risk_level="LOW",
        )
    finally:
        db.close()

    mine = client.get("/api/memory/history", headers={"X-Guest-Id": "device_aaa11111"})
    assert mine.status_code == 200
    tickers = {row["ticker"] for row in mine.json()}
    assert "NVDA" in tickers
    assert "AAPL" not in tickers

    friend = client.get("/api/memory/history", headers={"X-Guest-Id": "device_bbb22222"})
    assert friend.status_code == 200
    friend_tickers = {row["ticker"] for row in friend.json()}
    assert "AAPL" in friend_tickers
    assert "NVDA" not in friend_tickers


def test_history_empty_without_identity():
    r = client.get("/api/memory/history")
    assert r.status_code == 200
    assert r.json() == []


def test_auth_user_history_isolated_from_guest():
    headers = _auth_headers("owner@example.com")
    # Seed via SQL using the authenticated owner's key
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    user_id = me.json()["id"]

    db = SessionLocal()
    try:
        from app.db.models import User

        user = db.get(User, user_id)
        analysis_history_store.add_run(
            db,
            owner_key=f"user:{user_id}",
            user=user,
            ticker="MSFT",
            recommendation="HOLD",
            risk_level="MEDIUM",
        )
        analysis_history_store.add_run(
            db,
            owner_key="guest:someotherdevice99",
            user=None,
            ticker="TSLA",
            recommendation="SELL",
            risk_level="HIGH",
        )
    finally:
        db.close()

    hist = client.get("/api/memory/history", headers=headers)
    assert hist.status_code == 200
    tickers = {row["ticker"] for row in hist.json()}
    assert "MSFT" in tickers
    assert "TSLA" not in tickers


def test_resolve_owner_key_shapes():
    assert analysis_history_store.resolve_owner_key(None, "abc12345") == "guest:abc12345"
    assert analysis_history_store.resolve_owner_key(None, "bad") is None
    assert analysis_history_store.resolve_owner_key(None, None) is None

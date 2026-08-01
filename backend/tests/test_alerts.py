"""Phase 9 — smart alerts + redis fallback tests."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_phase9.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["REDIS_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import redis_store  # noqa: E402

init_db()
client = TestClient(app)


def _auth_headers(email: str = "alerts@example.com") -> dict:
    password = "securepass1"
    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    if reg.status_code == 409:
        reg = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
        )
    assert reg.status_code in (200, 201), reg.text
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def test_redis_memory_fallback():
    assert redis_store.redis_mode() == "memory"
    redis_store.cache_set("test:key", {"a": 1}, ttl_seconds=30)
    assert redis_store.cache_get("test:key") == {"a": 1}
    redis_store.queue_push({"job": "evaluate_alerts"})
    assert redis_store.queue_pop() == {"job": "evaluate_alerts"}


def test_alert_rules_crud_and_events():
    headers = _auth_headers()
    assert client.get("/api/alerts/rules").status_code == 401

    created = client.post(
        "/api/alerts/rules",
        headers=headers,
        json={
            "ticker": "AAPL",
            "alert_type": "price_above",
            "threshold": 1.0,
            "cooldown_minutes": 5,
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    rules = client.get("/api/alerts/rules", headers=headers)
    assert rules.status_code == 200
    assert any(r["id"] == rule_id for r in rules.json())

    evaluated = client.post("/api/alerts/evaluate", headers=headers)
    assert evaluated.status_code == 200, evaluated.text
    body = evaluated.json()
    assert body["evaluated_rules"] >= 1
    assert body["redis_mode"] == "memory"

    events = client.get("/api/alerts/events", headers=headers)
    assert events.status_code == 200

    deleted = client.delete(f"/api/alerts/rules/{rule_id}", headers=headers)
    assert deleted.status_code == 200


def test_news_sentiment_threshold_must_be_in_unit_range():
    headers = _auth_headers("sentiment@example.com")
    bad = client.post(
        "/api/alerts/rules",
        headers=headers,
        json={
            "ticker": "AAPL",
            "alert_type": "news_sentiment",
            "threshold": 200,
        },
    )
    assert bad.status_code == 422

    good = client.post(
        "/api/alerts/rules",
        headers=headers,
        json={
            "ticker": "AAPL",
            "alert_type": "news_sentiment",
            "threshold": 0.3,
        },
    )
    assert good.status_code == 201, good.text
    assert good.json()["threshold"] == 0.3

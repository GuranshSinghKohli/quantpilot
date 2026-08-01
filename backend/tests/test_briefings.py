"""Phase 8 — portfolio briefing API tests (SQLite, no OpenAI required)."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_phase8.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


def _auth_headers(email: str = "brief@example.com") -> dict:
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


def test_briefing_requires_auth():
    r = client.get("/api/portfolio/briefing")
    assert r.status_code == 401


def test_generate_briefing_empty_portfolio():
    headers = _auth_headers("empty-brief@example.com")
    missing = client.get("/api/portfolio/briefing", headers=headers)
    assert missing.status_code == 404

    generated = client.post("/api/portfolio/briefing/generate", headers=headers)
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["status"] in ("empty", "ok")
    assert "summary" in body

    latest = client.get("/api/portfolio/briefing", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["id"] == body["id"]


def test_generate_briefing_with_holding():
    headers = _auth_headers("held-brief@example.com")
    add = client.post("/api/watchlist/MSFT", headers=headers)
    assert add.status_code == 200

    generated = client.post(
        "/api/portfolio/briefing/generate",
        headers=headers,
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    assert body["headline"]
    assert body["summary"]
    assert isinstance(body["highlights"], list)

    listed = client.get("/api/portfolio/briefings?limit=3", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

"""Phase 7 — auth + DB-backed watchlist tests (SQLite)."""

import os
from pathlib import Path

# Force SQLite before app imports bind the engine
_TEST_DB = Path(__file__).resolve().parent / "_test_quantpilot.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_login_me_and_scoped_watchlist():
    email = "trader@example.com"
    password = "securepass1"

    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]
    assert reg.json()["user"]["email"] == email

    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == email

    empty = client.get("/api/watchlist", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == []

    added = client.post("/api/watchlist/AAPL", headers=headers)
    assert added.status_code == 200
    assert added.json()["ticker"] == "AAPL"

    listed = client.get("/api/watchlist", headers=headers)
    assert len(listed.json()) == 1
    assert listed.json()[0]["ticker"] == "AAPL"

    portfolio = client.get("/api/portfolio", headers=headers)
    assert portfolio.status_code == 200
    assert portfolio.json()["holdings_count"] == 1

    # Guest/anonymous list should not include the authenticated user's ticker
    guest = client.get("/api/watchlist")
    assert guest.status_code == 200
    guest_tickers = {e["ticker"] for e in guest.json()}
    assert "AAPL" not in guest_tickers

    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]


def test_duplicate_register_rejected():
    email = "dup@example.com"
    password = "securepass1"
    first = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert second.status_code == 409


def test_guest_watchlist_persists():
    r1 = client.post("/api/watchlist/MSFT")
    assert r1.status_code == 200
    r2 = client.get("/api/watchlist")
    assert any(e["ticker"] == "MSFT" for e in r2.json())
    client.delete("/api/watchlist/MSFT")

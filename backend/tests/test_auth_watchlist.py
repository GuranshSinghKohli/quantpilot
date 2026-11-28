"""Auth tests (watchlist / portfolio APIs were removed)."""

import os
from pathlib import Path

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


def test_register_login_me():
    email = "researcher@example.com"
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


def test_removed_trading_desk_routes():
    assert client.get("/api/watchlist").status_code == 404
    assert client.get("/api/portfolio").status_code == 404
    assert client.get("/api/recommendations").status_code == 404
    assert client.get("/api/alerts/rules").status_code == 404

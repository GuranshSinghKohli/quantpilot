"""Phase 11 extension — portfolio broker-sync agent + API tests."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_portfolio_sync.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents.portfolio_sync_agent import _regex_fallback, extract_positions  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

SAMPLE_PAGE = """
Positions
Symbol  Description        Qty       Price     Mkt Value
AAPL    Apple Inc          10.000    $225.50    $2,255.00
MSFT    Microsoft Corp     5.000     $410.20    $2,051.00
Cash    Money Market       -          -          $500.00
"""


def _auth_headers(email: str = "sync@example.com") -> dict:
    password = "securepass1"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    if reg.status_code == 409:
        reg = client.post("/api/auth/login", json={"email": email, "password": password})
    assert reg.status_code in (200, 201), reg.text
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def test_regex_fallback_extracts_rows():
    positions = _regex_fallback(SAMPLE_PAGE)
    tickers = {p.ticker for p in positions}
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "CASH" not in tickers


def test_extract_positions_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = asyncio.run(extract_positions(SAMPLE_PAGE, source="paste"))
    assert result.source == "paste"
    tickers = {p.ticker for p in result.positions}
    assert "AAPL" in tickers


def test_extract_positions_empty_text():
    result = asyncio.run(extract_positions("", source="paste"))
    assert result.positions == []
    assert result.confidence_score == 0.0


def test_sync_preview_requires_auth():
    r = client.post("/api/portfolio/sync/preview", json={"raw_text": SAMPLE_PAGE})
    assert r.status_code == 401


def test_sync_preview_and_confirm_flow():
    headers = _auth_headers()

    preview = client.post(
        "/api/portfolio/sync/preview",
        json={"raw_text": SAMPLE_PAGE, "use_openclaw": False},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert len(body["positions"]) >= 1
    tickers = {p["ticker"] for p in body["positions"]}
    assert "AAPL" in tickers

    confirm = client.post(
        "/api/portfolio/sync/confirm",
        json={"positions": body["positions"]},
        headers=headers,
    )
    assert confirm.status_code == 200, confirm.text
    summary = confirm.json()
    saved_tickers = {h["ticker"] for h in summary["holdings"]}
    assert "AAPL" in saved_tickers

    watchlist = client.get("/api/watchlist", headers=headers)
    assert watchlist.status_code == 200
    aapl_entry = next(e for e in watchlist.json() if e["ticker"] == "AAPL")
    assert aapl_entry["source"] == "broker_sync"
    assert aapl_entry["shares"] == 10.0


def test_sync_preview_openclaw_not_configured(monkeypatch):
    """Unconfigured OpenClaw must surface a 400 telling the user to paste instead.

    The MCP tool runs in a subprocess that inherits the developer's real env, so
    the disabled response is stubbed here to keep the assertion hermetic.
    """
    headers = _auth_headers("sync2@example.com")

    async def _not_configured(tool_name, arguments, timeout=None):
        return {
            "enabled": False,
            "provider": "not_configured",
            "url": "",
            "text": "",
            "error": "OpenClaw is not configured (OPENCLAW_BROWSER_URL unset).",
        }

    monkeypatch.setattr("app.routers.portfolio.call_tool", _not_configured)

    r = client.post(
        "/api/portfolio/sync/preview",
        json={"use_openclaw": True},
        headers=headers,
    )
    assert r.status_code == 400
    assert "OpenClaw" in r.json()["detail"]


def test_sync_confirm_requires_positions():
    headers = _auth_headers("sync3@example.com")
    r = client.post("/api/portfolio/sync/confirm", json={"positions": []}, headers=headers)
    assert r.status_code == 400

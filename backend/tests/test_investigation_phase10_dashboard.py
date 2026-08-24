"""Phase 10 — intelligence dashboard, company view, and alert feedback."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase10_dashboard.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.models import AlertEvent  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    EvidenceCreateRequest,
    InvestigationCreateRequest,
)
from app.services import company_memory, evidence_ledger_store  # noqa: E402

init_db()
client = TestClient(app)
GUEST_A = {"X-Guest-Id": "phase10dashowneraaaaaaaaaaaaaaaa"}
GUEST_B = {"X-Guest-Id": "phase10dashownerbbbbbbbbbbbbbbbb"}
OWNER_A = "guest:phase10dashowneraaaaaaaaaaaaaaaa"
OWNER_B = "guest:phase10dashownerbbbbbbbbbbbbbbbb"
EVENT_TIME = datetime(2026, 8, 20, 16, 0, tzinfo=timezone.utc)


def _create_case(db, owner: str, ticker: str = "NVDA", status: str = "complete"):
    case = evidence_ledger_store.create_investigation(
        db,
        owner_key=owner,
        user=None,
        body=InvestigationCreateRequest(
            ticker=ticker,
            trigger_reason="scheduled",
            move_pct=-8.7,
            window_label="1d",
            summary=f"Investigating {ticker} decline.",
        ),
    )
    if status != "planning":
        evidence_ledger_store.set_status(
            db,
            owner_key=owner,
            investigation_id=case.id,
            status=status,
            summary=f"{ticker} guidance concerns are the strongest supported explanation.",
        )
    return case


def _auth(email: str) -> tuple[dict, int]:
    password = "securepass1"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    if reg.status_code == 409:
        reg = client.post("/api/auth/login", json={"email": email, "password": password})
    assert reg.status_code in (200, 201), reg.text
    return (
        {"Authorization": f"Bearer {reg.json()['access_token']}"},
        int(reg.json()["user"]["id"]),
    )


def test_dashboard_requires_owner_scope():
    missing = client.get("/api/dashboard")
    assert missing.status_code == 400


def test_dashboard_empty_state_has_no_invented_movers():
    empty = {"X-Guest-Id": "phase10dashemptycccccccccccccccc"}
    res = client.get("/api/dashboard", headers=empty)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["counts"]["investigations_today"] == 0
    assert body["counts"]["active"] == 0
    assert body["active"] == []
    assert body["recent"] == []
    assert body["alerts"] == []
    assert body["activity"] == []
    assert body["company_updates"] == []
    assert body["portfolio"]["total_market_value"] is None
    assert body["portfolio"]["holdings_count"] == 0
    assert "invent" in body["notes"][1].lower() or "placeholder" in body["notes"][1].lower()


def test_dashboard_loads_owner_investigations_and_isolates_guests():
    db = SessionLocal()
    try:
        mine = _create_case(db, OWNER_A, "NVDA", status="collecting")
        _create_case(db, OWNER_B, "AAPL", status="complete")
    finally:
        db.close()

    a = client.get("/api/dashboard", headers=GUEST_A)
    assert a.status_code == 200, a.text
    payload = a.json()
    tickers = {row["ticker"] for row in payload["recent"]}
    assert "NVDA" in tickers
    assert "AAPL" not in tickers
    assert payload["counts"]["active"] >= 1
    assert any(row["id"] == mine.id for row in payload["active"])
    assert any(item["ticker"] == "NVDA" for item in payload["activity"])

    b = client.get("/api/dashboard", headers=GUEST_B)
    assert b.status_code == 200
    other = {row["ticker"] for row in b.json()["recent"]}
    assert "AAPL" in other
    assert "NVDA" not in other


def test_existing_investigation_list_still_compatible():
    listed = client.get("/api/investigations", headers=GUEST_A)
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)


def test_unauthorized_investigation_is_not_found():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A, "MSFT", status="complete")
        case_id = case.id
    finally:
        db.close()
    denied = client.get(f"/api/investigations/{case_id}", headers=GUEST_B)
    assert denied.status_code == 404
    allowed = client.get(f"/api/investigations/{case_id}", headers=GUEST_A)
    assert allowed.status_code == 200
    assert allowed.json()["ticker"] == "MSFT"


def test_company_intelligence_uses_stored_memory_only():
    db = SessionLocal()
    try:
        case = _create_case(db, OWNER_A, "NVDA", status="complete")
        ev = evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER_A,
            investigation_id=case.id,
            body=EvidenceCreateRequest(
                source_type="ir_page",
                retrieval_method="mcp",
                title="NVIDIA earnings presentation",
                excerpt="Management cited weaker margin guidance.",
                source_url="https://investor.nvidia.com/q2",
            ),
        )
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "RISK",
                "statement": "Export restrictions remain a recurring regulatory risk.",
                "theme_key": "export_restrictions",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
                "evidence_id": ev.id,
                "source_url": "https://investor.nvidia.com/q2",
                "source_type": "ir_page",
            },
        )
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "MANAGEMENT_STATEMENT",
                "statement": "Management expects continued strong AI infrastructure demand.",
                "theme_key": "ai_demand",
                "polarity": "POSITIVE",
                "observed_at": datetime(2026, 2, 20, tzinfo=timezone.utc),
                "investigation_id": case.id,
                "evidence_id": ev.id,
                "source_url": "https://investor.nvidia.com/q2",
                "source_type": "ir_page",
            },
        )
        company_memory.upsert_memory(
            db,
            owner_key=OWNER_A,
            candidate={
                "ticker": "NVDA",
                "memory_type": "MANAGEMENT_STATEMENT",
                "statement": "Management now describes AI demand as more moderate.",
                "theme_key": "ai_demand",
                "polarity": "NEGATIVE",
                "observed_at": EVENT_TIME,
                "investigation_id": case.id,
                "evidence_id": ev.id,
                "source_url": "https://investor.nvidia.com/q2",
                "source_type": "ir_page",
            },
        )
    finally:
        db.close()

    mine = client.get("/api/companies/NVDA/intelligence", headers=GUEST_A)
    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["ticker"] == "NVDA"
    assert any("Export restrictions" in risk for risk in body["memory"]["recurring_risks"])
    assert body["management_trend"]
    assert body["timeline"]
    assert any(row["ticker"] == "NVDA" for row in body["investigations"])

    other = client.get("/api/companies/NVDA/intelligence", headers=GUEST_B)
    assert other.status_code == 200
    assert other.json()["memory"]["recurring_risks"] == []
    assert "Not enough historical intelligence yet." in other.json()["notes"]


def test_legacy_memory_endpoint_still_works():
    res = client.get("/api/investigations/memory/NVDA", headers=GUEST_A)
    assert res.status_code == 200
    assert res.json()["ticker"] == "NVDA"


def test_alert_feedback_is_owner_scoped():
    headers_a, user_a = _auth("phase10dash-a@example.com")
    headers_b, _user_b = _auth("phase10dash-b@example.com")
    db = SessionLocal()
    try:
        event = AlertEvent(
            user_id=user_a,
            ticker="NVDA",
            alert_type="investigation",
            title="NVDA material decline investigated",
            message="Guidance concerns are the strongest supported explanation.",
            materiality_score=0.82,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        event_id = event.id
    finally:
        db.close()

    denied = client.post(
        f"/api/alerts/events/{event_id}/feedback",
        headers=headers_b,
        json={"feedback": "useful"},
    )
    assert denied.status_code == 404

    ok = client.post(
        f"/api/alerts/events/{event_id}/feedback",
        headers=headers_a,
        json={"feedback": "useful"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["feedback"] == "useful"

    dash = client.get("/api/dashboard", headers=headers_a)
    assert dash.status_code == 200
    titles = [a["title"] for a in dash.json()["alerts"]]
    assert any("NVDA" in t for t in titles)

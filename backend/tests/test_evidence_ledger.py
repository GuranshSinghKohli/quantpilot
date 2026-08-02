"""PRD v3 Phase 7 — Evidence Ledger ownership + CRUD smoke tests."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_evidence_ledger.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402

init_db()
client = TestClient(app)

GUEST_A = {"X-Guest-Id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
GUEST_B = {"X-Guest-Id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}


def test_create_list_and_detail_are_guest_scoped():
    created = client.post(
        "/api/investigations",
        headers=GUEST_A,
        json={"ticker": "nvda", "trigger_reason": "on_demand"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["ticker"] == "NVDA"
    assert body["status"] == "planning"
    inv_id = body["id"]

    list_a = client.get("/api/investigations", headers=GUEST_A)
    assert list_a.status_code == 200
    assert any(row["id"] == inv_id for row in list_a.json())

    list_b = client.get("/api/investigations", headers=GUEST_B)
    assert list_b.status_code == 200
    assert all(row["id"] != inv_id for row in list_b.json())

    denied = client.get(f"/api/investigations/{inv_id}", headers=GUEST_B)
    assert denied.status_code == 404


def test_evidence_claim_link_and_complete_flow():
    created = client.post(
        "/api/investigations",
        headers=GUEST_A,
        json={
            "ticker": "AAPL",
            "trigger_reason": "price_move",
            "move_pct": -3.2,
            "window_label": "1d",
        },
    )
    assert created.status_code == 201, created.text
    inv_id = created.json()["id"]

    evidence = client.post(
        f"/api/investigations/{inv_id}/evidence",
        headers=GUEST_A,
        json={
            "source_type": "news",
            "retrieval_method": "user",
            "title": "Supplier delay headline",
            "excerpt": "AAPL suppliers reported shipment delays.",
            "source_url": "https://example.com/aapl",
        },
    )
    assert evidence.status_code == 201, evidence.text
    evidence_id = evidence.json()["id"]

    claim = client.post(
        f"/api/investigations/{inv_id}/claims",
        headers=GUEST_A,
        json={
            "statement": "Move driven by supply-chain delay fears",
            "stance": "supports_move",
            "confidence_score": 0.62,
            "rank": 1,
            "evidence_ids": [evidence_id],
        },
    )
    assert claim.status_code == 201, claim.text
    claim_body = claim.json()
    assert claim_body["stance"] == "supports_move"
    assert len(claim_body["evidence_links"]) >= 1

    link = client.post(
        f"/api/investigations/{inv_id}/links",
        headers=GUEST_A,
        json={
            "claim_id": claim_body["id"],
            "evidence_id": evidence_id,
            "relation": "context",
            "note": "timing unclear",
        },
    )
    assert link.status_code == 201, link.text
    assert link.json()["relation"] == "context"

    done = client.post(
        f"/api/investigations/{inv_id}/complete",
        headers=GUEST_A,
        params={"summary": "Likely idiosyncratic; not broad tech selloff."},
    )
    assert done.status_code == 200, done.text
    detail = done.json()
    assert detail["status"] == "complete"
    assert detail["claims_count"] == 1
    assert detail["evidence_count"] == 1
    assert "idiosyncratic" in detail["summary"]


def test_requires_owner_header_for_mutations():
    res = client.post(
        "/api/investigations",
        json={"ticker": "MSFT"},
    )
    assert res.status_code == 400

"""PRD v3 FR-10 — natural language search over investigations/evidence."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_fr10.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["INVESTIGATIONS_SWEEP_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
# Force keyword path (no OpenAI embeddings required).
os.environ.pop("OPENAI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.investigation_schemas import (  # noqa: E402
    ClaimCreateRequest,
    EvidenceCreateRequest,
    InvestigationCreateRequest,
)
from app.services import evidence_ledger_store, investigation_search  # noqa: E402

init_db()
client = TestClient(app)

GUEST = {"X-Guest-Id": "fr10guestaaaaaaaaaaaaaaaaaaaaaaa"}
OWNER = "guest:fr10guestaaaaaaaaaaaaaaaaaaaaaaa"


def _seed_case() -> int:
    db = SessionLocal()
    try:
        created = evidence_ledger_store.create_investigation(
            db,
            owner_key=OWNER,
            user=None,
            body=InvestigationCreateRequest(
                ticker="NVDA",
                trigger_reason="on_demand",
                summary="Investigating NVDA drop amid export curb chatter.",
            ),
        )
        assert created is not None
        evidence_ledger_store.add_evidence(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=EvidenceCreateRequest(
                source_type="news",
                retrieval_method="user",
                title="Chip export curb fears",
                excerpt="Headlines about export restrictions hitting semis.",
            ),
        )
        evidence_ledger_store.add_claim(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            body=ClaimCreateRequest(
                statement="Export curb fears drove the drop",
                stance="supports_move",
                confidence_score=0.7,
                rank=1,
            ),
        )
        evidence_ledger_store.mark_complete(
            db,
            owner_key=OWNER,
            investigation_id=created.id,
            summary="Leading explanation is export-related news.",
        )
        return created.id
    finally:
        db.close()


def test_tokenize_query():
    assert "nvda" in investigation_search.tokenize_query("Why did NVDA move on export curbs?")
    assert "the" not in investigation_search.tokenize_query("the move")


def test_search_finds_claim_and_evidence():
    inv_id = _seed_case()
    res = client.get(
        "/api/investigations/search",
        params={"q": "export curb fears"},
        headers=GUEST,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "keyword"
    assert any(r["investigation_id"] == inv_id for r in body["results"])
    hit = next(r for r in body["results"] if r["investigation_id"] == inv_id)
    assert "claim" in hit["match_sources"] or "evidence" in hit["match_sources"]
    assert hit["ticker"] == "NVDA"


def test_search_by_ticker():
    _seed_case()
    res = client.get(
        "/api/investigations/search",
        params={"q": "NVDA"},
        headers=GUEST,
    )
    assert res.status_code == 200
    assert any(r["ticker"] == "NVDA" for r in res.json()["results"])


def test_search_scoped_to_owner():
    _seed_case()
    other = {"X-Guest-Id": "fr10otherbbbbbbbbbbbbbbbbbbbb"}
    res = client.get(
        "/api/investigations/search",
        params={"q": "export curb"},
        headers=other,
    )
    assert res.status_code == 200
    assert res.json()["results"] == []


def test_search_requires_scope_returns_empty_without_guest():
    res = client.get("/api/investigations/search", params={"q": "NVDA"})
    assert res.status_code == 200
    assert res.json()["results"] == []

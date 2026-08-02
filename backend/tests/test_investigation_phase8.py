"""PRD v3 Phase 8 — reactive investigation pipeline tests."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase8.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["INVESTIGATION_NOISE_PCT"] = "1.5"

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()
client = TestClient(app)

GUEST = {"X-Guest-Id": "phase8guestaaaaaaaaaaaaaaaaaaaa"}


def _move(pct: float = -4.2, noise: bool = False) -> MoveSnapshot:
    return MoveSnapshot(
        ticker="NVDA",
        move_pct=pct,
        window_label="1d",
        current_price=100.0,
        previous_close=104.4,
        is_noise=noise,
        source="test",
        detail=f"NVDA moved {pct}% over 1d",
    )


def test_investigate_endpoint_fills_ledger():
    plan = {
        "focus": "Why NVDA fell",
        "tools": ["get_stock_news"],
        "seed_hypotheses": [
            "Chip export news",
            "Market beta",
            "Unknown",
        ],
        "skip_reason": None,
    }
    evidence = [
        {
            "source_type": "price",
            "retrieval_method": "system",
            "title": "NVDA price move",
            "excerpt": "down 4.2%",
            "source_url": "",
            "raw_payload": {},
        },
        {
            "source_type": "news",
            "retrieval_method": "mcp",
            "title": "Export curb chatter",
            "excerpt": "Headline about export rules",
            "source_url": "https://example.com/n",
            "raw_payload": {},
        },
    ]
    ranked = {
        "summary": "Leading explanation is export-related news (weight 55%).",
        "hypotheses": [
            {
                "statement": "Export curb fears drove the drop",
                "stance": "supports_move",
                "confidence_score": 0.7,
                "weight": 0.55,
                "devil_advocate_notes": "Could be broad semiconductor beta.",
                "evidence_indices": [1],
                "rank": 1,
            },
            {
                "statement": "Moved with the sector; little idiosyncratic news",
                "stance": "market_noise",
                "confidence_score": 0.45,
                "weight": 0.3,
                "devil_advocate_notes": "The export headline is company-relevant.",
                "evidence_indices": [0],
                "rank": 2,
            },
            {
                "statement": "Insufficient evidence",
                "stance": "unknown",
                "confidence_score": 0.25,
                "weight": 0.15,
                "devil_advocate_notes": "More IR materials may clarify.",
                "evidence_indices": [0],
                "rank": 3,
            },
        ],
    }

    verified = {
        "hypotheses": ranked["hypotheses"],
        "rejected": [],
        "notes": ["All claims grounded."],
        "citation_coverage": 1.0,
    }
    da = {
        "hypotheses": ranked["hypotheses"],
        "outcome": "held",
        "counterargument": "Sector beta remains a plausible alternate.",
        "leading_weakened": False,
        "reversal": False,
        "notes": [],
        "confidence_delta": 0.0,
    }

    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(return_value=_move(-4.2)),
    ), patch(
        "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
        new=AsyncMock(return_value=plan),
    ), patch(
        "app.services.investigation_runner._collect_evidence",
        new=AsyncMock(return_value=evidence),
    ), patch(
        "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
        new=AsyncMock(return_value=ranked),
    ), patch(
        "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
        new=AsyncMock(return_value=verified),
    ), patch(
        "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
        new=AsyncMock(return_value=da),
    ):
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={"ticker": "nvda", "window_label": "1d"},
        )

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["ticker"] == "NVDA"
    assert body["status"] == "complete"
    assert body["move_pct"] == -4.2
    assert body["claims_count"] == 3
    assert body["evidence_count"] == 2
    assert body["claims"][0]["stance"] == "supports_move"
    assert body["claims"][0]["evidence_links"]
    assert "export" in body["summary"].lower() or "weight" in body["summary"].lower()


def test_skip_if_noise_marks_skipped():
    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(return_value=_move(0.4, noise=True)),
    ):
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={
                "ticker": "AAPL",
                "skip_if_noise": True,
                "window_label": "1d",
            },
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "skipped_market_noise"
    assert body["claims_count"] >= 1
    assert body["claims"][0]["stance"] == "market_noise"


def test_run_on_existing_investigation():
    created = client.post(
        "/api/investigations",
        headers=GUEST,
        json={"ticker": "MSFT", "trigger_reason": "on_demand"},
    )
    assert created.status_code == 201, created.text
    inv_id = created.json()["id"]

    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(return_value=MoveSnapshot(
            ticker="MSFT",
            move_pct=2.5,
            window_label="1d",
            current_price=400.0,
            previous_close=390.0,
            is_noise=False,
            source="test",
            detail="MSFT up 2.5%",
        )),
    ), patch(
        "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
        new=AsyncMock(
            return_value={
                "focus": "MSFT up",
                "tools": ["get_stock_news"],
                "seed_hypotheses": ["Earnings optimism", "Market beta"],
                "skip_reason": None,
            }
        ),
    ), patch(
        "app.services.investigation_runner._collect_evidence",
        new=AsyncMock(
            return_value=[
                {
                    "source_type": "price",
                    "retrieval_method": "system",
                    "title": "move",
                    "excerpt": "up",
                    "source_url": "",
                    "raw_payload": {},
                }
            ]
        ),
    ), patch(
        "app.services.investigation_runner.hypothesis_ranker_agent.rank_hypotheses",
        new=AsyncMock(
            return_value={
                "summary": "Weighted hypotheses ready.",
                "hypotheses": [
                    {
                        "statement": "Soft catalyst / beta",
                        "stance": "market_noise",
                        "confidence_score": 0.6,
                        "weight": 0.6,
                        "devil_advocate_notes": "Maybe product news.",
                        "evidence_indices": [0],
                        "rank": 1,
                    }
                ],
            }
        ),
    ), patch(
        "app.services.investigation_runner.investigation_verification_agent.verify_hypotheses",
        new=AsyncMock(
            return_value={
                "hypotheses": [
                    {
                        "statement": "Soft catalyst / beta",
                        "stance": "market_noise",
                        "confidence_score": 0.6,
                        "weight": 0.6,
                        "devil_advocate_notes": "Maybe product news.",
                        "evidence_indices": [0],
                        "rank": 1,
                    }
                ],
                "rejected": [],
                "notes": ["ok"],
                "citation_coverage": 1.0,
            }
        ),
    ), patch(
        "app.services.investigation_runner.devils_advocate_agent.stress_test_leading",
        new=AsyncMock(
            return_value={
                "hypotheses": [
                    {
                        "statement": "Soft catalyst / beta",
                        "stance": "market_noise",
                        "confidence_score": 0.6,
                        "weight": 0.6,
                        "devil_advocate_notes": "Maybe product news.",
                        "evidence_indices": [0],
                        "rank": 1,
                    }
                ],
                "outcome": "held",
                "counterargument": "Still plausible.",
                "leading_weakened": False,
                "reversal": False,
                "notes": [],
                "confidence_delta": 0.0,
            }
        ),
    ):
        res = client.post(
            f"/api/investigations/{inv_id}/run",
            headers=GUEST,
            json={"window_label": "1d"},
        )

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "complete"
    assert res.json()["claims_count"] >= 1

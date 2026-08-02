"""PRD v3 Phase 9 — Verification + Devil's Advocate in investigation flow."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase9.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import devils_advocate_agent, investigation_verification_agent  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()
client = TestClient(app)
GUEST = {"X-Guest-Id": "phase9guestaaaaaaaaaaaaaaaaaaaa"}


def test_verification_rejects_ungrounded_company_claim():
    evidence = [
        {
            "source_type": "price",
            "title": "move",
            "excerpt": "down 3%",
        }
    ]
    hypotheses = [
        {
            "statement": "Secret product delay nobody reported",
            "stance": "supports_move",
            "weight": 0.7,
            "confidence_score": 0.7,
            "evidence_indices": [],
            "devil_advocate_notes": "",
            "rank": 1,
        },
        {
            "statement": "Market beta",
            "stance": "market_noise",
            "weight": 0.3,
            "confidence_score": 0.3,
            "evidence_indices": [],
            "devil_advocate_notes": "",
            "rank": 2,
        },
    ]

    result = asyncio.get_event_loop().run_until_complete(
        investigation_verification_agent.verify_hypotheses("TEST", hypotheses, evidence)
    )
    assert result["hypotheses"]
    for h in result["hypotheses"]:
        assert h.get("evidence_indices"), "FR-3: every claim must link evidence"
    # Ungrounded company-specific claim should be rejected (or only market_noise kept).
    kept_stances = {h["stance"] for h in result["hypotheses"]}
    assert "supports_move" not in kept_stances or all(
        h.get("evidence_indices") for h in result["hypotheses"] if h["stance"] == "supports_move"
    )
    assert any(h["stance"] == "market_noise" for h in result["hypotheses"])


def test_devils_advocate_can_demote_leading():
    move = MoveSnapshot(
        ticker="XYZ",
        move_pct=-3.0,
        window_label="1d",
        current_price=10.0,
        previous_close=10.3,
        is_noise=False,
        source="test",
        detail="XYZ down",
    )
    hypotheses = [
        {
            "statement": "Company-specific scare",
            "stance": "supports_move",
            "weight": 0.7,
            "confidence_score": 0.7,
            "evidence_indices": [0],
            "devil_advocate_notes": "",
            "rank": 1,
        },
        {
            "statement": "Sector beta",
            "stance": "market_noise",
            "weight": 0.3,
            "confidence_score": 0.3,
            "evidence_indices": [0],
            "devil_advocate_notes": "",
            "rank": 2,
        },
    ]
    # No news/filings → heuristic DA demotes company-specific lead.
    evidence = [{"source_type": "price", "title": "px", "excerpt": "down"}]
    result = asyncio.get_event_loop().run_until_complete(
        devils_advocate_agent.stress_test_leading(move, hypotheses, evidence)
    )
    assert result["leading_weakened"] is True
    assert result["outcome"] in ("demoted", "confidence_cut")
    assert result["hypotheses"][0]["devil_advocate_notes"]


def test_investigate_persists_da_outcome():
    evidence = [
        {
            "source_type": "price",
            "retrieval_method": "system",
            "title": "move",
            "excerpt": "up",
            "source_url": "",
            "raw_payload": {},
        },
        {
            "source_type": "news",
            "retrieval_method": "mcp",
            "title": "Product launch",
            "excerpt": "Launch headline",
            "source_url": "https://example.com",
            "raw_payload": {},
        },
    ]
    ranked = {
        "summary": "Draft ranking",
        "hypotheses": [
            {
                "statement": "Product launch drove the move",
                "stance": "supports_move",
                "confidence_score": 0.65,
                "weight": 0.65,
                "devil_advocate_notes": "",
                "evidence_indices": [1],
                "rank": 1,
            },
            {
                "statement": "Broad market lift",
                "stance": "market_noise",
                "confidence_score": 0.35,
                "weight": 0.35,
                "devil_advocate_notes": "",
                "evidence_indices": [0],
                "rank": 2,
            },
        ],
    }
    verified = {
        "hypotheses": ranked["hypotheses"],
        "rejected": [],
        "notes": ["Grounded."],
        "citation_coverage": 1.0,
    }
    h0 = dict(ranked["hypotheses"][0])
    h1 = dict(ranked["hypotheses"][1])
    h1.update(
        {
            "weight": 0.55,
            "rank": 1,
            "devil_advocate_notes": "Launch narrative overfits a single headline.",
        }
    )
    h0.update(
        {
            "weight": 0.45,
            "rank": 2,
            "devil_advocate_notes": "Launch narrative overfits a single headline.",
        }
    )
    demoted = {
        "hypotheses": [h1, h0],
        "outcome": "demoted",
        "counterargument": "Launch narrative overfits a single headline.",
        "leading_weakened": True,
        "reversal": True,
        "notes": ["Promoted market_noise competitor."],
        "confidence_delta": -0.2,
    }

    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(
            return_value=MoveSnapshot(
                ticker="TSLA",
                move_pct=2.2,
                window_label="1d",
                current_price=200.0,
                previous_close=195.0,
                is_noise=False,
                source="test",
                detail="TSLA up",
            )
        ),
    ), patch(
        "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
        new=AsyncMock(
            return_value={
                "focus": "TSLA",
                "tools": ["get_stock_news"],
                "seed_hypotheses": ["launch", "beta"],
                "skip_reason": None,
            }
        ),
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
        new=AsyncMock(return_value=demoted),
    ):
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={"ticker": "TSLA"},
        )

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "complete"
    assert body["da_outcome"] is not None
    assert body["da_outcome"]["outcome"] == "demoted"
    assert body["da_outcome"]["reversal"] is True
    assert body["claims"][0]["stance"] == "market_noise"
    assert all(c["evidence_links"] for c in body["claims"])

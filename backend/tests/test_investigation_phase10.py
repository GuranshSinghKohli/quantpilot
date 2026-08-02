"""PRD v3 Phase 10 — Browser MCP IR evidence in investigation flow."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

_TEST_DB = Path(__file__).resolve().parent / "_test_investigation_phase10.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["QUANTPILOT_LIGHTWEIGHT"] = "true"
os.environ["BROWSER_MCP_ENABLED"] = "true"

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.agents import investigation_planner_agent  # noqa: E402
from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import investigation_runner  # noqa: E402
from app.services.move_detector import MoveSnapshot  # noqa: E402

init_db()
client = TestClient(app)
GUEST = {"X-Guest-Id": "phase10guestaaaaaaaaaaaaaaaaaaa"}


def test_planner_includes_ir_for_large_downside_move():
    move = MoveSnapshot(
        ticker="NVDA",
        move_pct=-5.2,
        window_label="1d",
        current_price=100.0,
        previous_close=105.5,
        is_noise=False,
        source="test",
        detail="NVDA down",
    )
    plan = asyncio.get_event_loop().run_until_complete(
        investigation_planner_agent.plan_investigation(move)
    )
    assert "get_ir_materials" in plan["tools"]
    assert plan.get("need_browser_ir") is True


def test_map_ir_materials_pages_shape():
    result = {
        "ticker": "AAPL",
        "enabled": True,
        "provider": "httpx",
        "pages": [
            {
                "url": "https://investor.apple.com/",
                "title": "Apple Investor Relations",
                "provider": "httpx",
                "text_excerpt": "Welcome to Apple IR " * 20,
                "char_count": 400,
            }
        ],
        "sources": ["https://investor.apple.com/"],
        "excerpt": "Welcome to Apple IR",
        "error": "",
    }
    rows = investigation_runner._map_browser_evidence(
        "AAPL", "get_ir_materials", result
    )
    assert len(rows) == 1
    assert rows[0]["source_type"] == "ir_page"
    assert rows[0]["retrieval_method"] == "httpx"
    assert rows[0]["source_url"].startswith("https://investor.apple.com")
    assert "Apple" in rows[0]["title"] or "IR" in rows[0]["title"]


def test_thin_api_evidence_escalates_to_browser_mcp():
    move = MoveSnapshot(
        ticker="MSFT",
        move_pct=-3.1,
        window_label="1d",
        current_price=400.0,
        previous_close=412.0,
        is_noise=False,
        source="test",
        detail="MSFT down",
    )
    plan = {
        "focus": "MSFT",
        "tools": ["get_stock_news"],
        "seed_hypotheses": ["news", "beta"],
        "need_browser_ir": False,
    }

    async def fake_tool(ticker: str, tool: str):
        if tool == "get_stock_news":
            return [
                {
                    "source_type": "news",
                    "retrieval_method": "mcp",
                    "title": "Only one headline",
                    "excerpt": "thin",
                    "source_url": "",
                    "raw_payload": {},
                }
            ]
        if tool == "get_ir_materials":
            return [
                {
                    "source_type": "ir_page",
                    "retrieval_method": "httpx",
                    "title": "MSFT IR",
                    "excerpt": "Investor relations content " * 10,
                    "source_url": "https://www.microsoft.com/en-us/investor",
                    "raw_payload": {},
                }
            ]
        if tool == "get_shareholder_letter":
            return []
        return []

    with patch(
        "app.services.investigation_runner._evidence_from_tool",
        new=AsyncMock(side_effect=fake_tool),
    ):
        items = asyncio.get_event_loop().run_until_complete(
            investigation_runner._collect_evidence(move, plan)
        )

    assert any(i.get("source_type") == "ir_page" for i in items)


def test_investigate_persists_ir_evidence():
    evidence = [
        {
            "source_type": "price",
            "retrieval_method": "system",
            "title": "move",
            "excerpt": "down",
            "source_url": "",
            "raw_payload": {},
        },
        {
            "source_type": "ir_page",
            "retrieval_method": "httpx",
            "title": "NVIDIA Investor Relations",
            "excerpt": "Official IR materials discussing recent developments.",
            "source_url": "https://investor.nvidia.com/home/default.aspx",
            "raw_payload": {"provider": "httpx"},
        },
    ]
    ranked = {
        "summary": "IR-backed ranking",
        "hypotheses": [
            {
                "statement": "Company IR narrative supports idiosyncratic move",
                "stance": "supports_move",
                "confidence_score": 0.55,
                "weight": 0.55,
                "devil_advocate_notes": "",
                "evidence_indices": [1],
                "rank": 1,
            },
            {
                "statement": "Market beta",
                "stance": "market_noise",
                "confidence_score": 0.45,
                "weight": 0.45,
                "devil_advocate_notes": "",
                "evidence_indices": [0],
                "rank": 2,
            },
        ],
    }
    verified = {
        "hypotheses": ranked["hypotheses"],
        "rejected": [],
        "notes": ["IR grounded."],
        "citation_coverage": 1.0,
    }
    da = {
        "hypotheses": ranked["hypotheses"],
        "outcome": "held",
        "counterargument": "Still could be sector beta.",
        "leading_weakened": False,
        "reversal": False,
        "notes": [],
        "confidence_delta": 0.0,
    }

    with patch(
        "app.services.investigation_runner.detect_move",
        new=AsyncMock(
            return_value=MoveSnapshot(
                ticker="NVDA",
                move_pct=-4.0,
                window_label="1d",
                current_price=100.0,
                previous_close=104.0,
                is_noise=False,
                source="test",
                detail="NVDA down",
            )
        ),
    ), patch(
        "app.services.investigation_runner.investigation_planner_agent.plan_investigation",
        new=AsyncMock(
            return_value={
                "focus": "NVDA",
                "tools": ["get_stock_news", "get_ir_materials"],
                "seed_hypotheses": ["IR", "beta"],
                "need_browser_ir": True,
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
        new=AsyncMock(return_value=da),
    ):
        res = client.post(
            "/api/investigations/investigate",
            headers=GUEST,
            json={"ticker": "NVDA"},
        )

    assert res.status_code == 201, res.text
    body = res.json()
    ir = [e for e in body["evidence_items"] if e["source_type"] == "ir_page"]
    assert ir, body["evidence_items"]
    assert ir[0]["retrieval_method"] == "httpx"
    assert "nvidia.com" in (ir[0].get("source_url") or "")

"""PRD v3 Phase 8/10 — Investigation Planner: what evidence to gather for this move."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

logger = get_logger("investigation_planner")

VALID_TOOLS = {
    "get_stock_news",
    "get_recent_filings",
    "get_stock_price",
    "get_price_history",
    "get_ir_materials",
    "get_shareholder_letter",
    "get_stock_fundamentals",
}

BROWSER_TOOLS = {"get_ir_materials", "get_shareholder_letter"}


def browser_mcp_enabled() -> bool:
    return os.getenv("BROWSER_MCP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def plan_investigation(
    move: MoveSnapshot,
    *,
    asset_hint: str = "equity",
) -> Dict[str, Any]:
    """Return plan: tools to call, seed hypotheses, focus questions."""
    hint = (asset_hint or getattr(move, "asset_class", None) or "equity").lower()
    fallback = _fallback_plan(move, asset_hint=hint)
    payload = {
        "ticker": move.ticker,
        "move_pct": move.move_pct,
        "window_label": move.window_label,
        "is_noise": move.is_noise,
        "current_price": move.current_price,
        "asset_hint": hint,
        "depth": getattr(move, "depth", "standard"),
        "residual_pct": getattr(move, "residual_pct", None),
        "move_zscore": getattr(move, "move_zscore", None),
        "move_detail": move.detail,
        "browser_mcp_enabled": browser_mcp_enabled(),
    }

    available = sorted(
        t
        for t in VALID_TOOLS
        if t not in BROWSER_TOOLS or browser_mcp_enabled()
    )

    result = await call_openai_json(
        system_prompt=(
            "You are QuantPilot's Investigation Planner. Given a price move, decide "
            "what evidence is needed to explain WHY it moved — not a full research report. "
            "Respond ONLY with JSON matching: "
            '{"focus": string, "tools": [string, ...], '
            '"seed_hypotheses": [string, ...], "skip_reason": string|null, '
            '"need_browser_ir": boolean}. '
            f"tools must be chosen from: {available}. "
            "Prefer 2-5 tools. For asset_hint=etf prefer news/price/fundamentals and "
            "avoid filings/IR/shareholder letters unless the move is extreme. "
            "For equities, include get_ir_materials when company-specific / large / downside "
            "(need_browser_ir=true). seed_hypotheses: 3-5 short competing explanations."
        ),
        user_prompt=f"Plan investigation:\n{json.dumps(payload, default=str)}",
        fallback=fallback,
    )

    tools = [
        t
        for t in (result.get("tools") or [])
        if isinstance(t, str) and t in VALID_TOOLS
    ]
    if not tools:
        tools = list(fallback["tools"])

    if not browser_mcp_enabled():
        tools = [t for t in tools if t not in BROWSER_TOOLS]
    else:
        want_ir = bool(
            result.get("need_browser_ir") or fallback.get("need_browser_ir")
        )
        if want_ir and "get_ir_materials" not in tools:
            tools.append("get_ir_materials")
        for t in fallback["tools"]:
            if t in BROWSER_TOOLS and t not in tools and want_ir:
                tools.append(t)

    if hint == "etf":
        # ETF path: avoid equity-centric IR/filing tools unless depth is deep.
        depth = getattr(move, "depth", "standard") or "standard"
        drop = {"get_recent_filings", "get_shareholder_letter"}
        if depth != "deep":
            drop.add("get_ir_materials")
        tools = [t for t in tools if t not in drop]
        if "get_price_history" not in tools:
            tools.append("get_price_history")
        if "get_stock_fundamentals" not in tools:
            tools.append("get_stock_fundamentals")

    seen = set()
    ordered_tools: List[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            ordered_tools.append(t)

    hypotheses = [
        h.strip()
        for h in (result.get("seed_hypotheses") or [])
        if isinstance(h, str) and h.strip()
    ][:5]
    if not hypotheses:
        hypotheses = list(fallback["seed_hypotheses"])

    plan = {
        "focus": (result.get("focus") or fallback["focus"])[:500],
        "tools": ordered_tools,
        "seed_hypotheses": hypotheses,
        "skip_reason": result.get("skip_reason"),
        "need_browser_ir": bool(
            result.get("need_browser_ir")
            or any(t in BROWSER_TOOLS for t in ordered_tools)
        ),
    }
    log_event(
        logger,
        logging.INFO,
        "Investigation plan ready",
        ticker=move.ticker,
        tools=ordered_tools,
        hypothesis_count=len(hypotheses),
        need_browser_ir=plan["need_browser_ir"],
    )
    return plan


def _fallback_plan(move: MoveSnapshot, asset_hint: str = "equity") -> Dict[str, Any]:
    direction = "rose" if (move.move_pct or 0) >= 0 else "fell"
    pct = f"{abs(move.move_pct):.2f}%" if move.move_pct is not None else "an unknown amount"
    abs_move = abs(move.move_pct or 0)
    depth = getattr(move, "depth", "standard") or "standard"
    is_etf = asset_hint == "etf"

    if is_etf:
        tools: List[str] = [
            "get_stock_news",
            "get_stock_price",
            "get_price_history",
            "get_stock_fundamentals",
        ]
        need_ir = False
        focus = (
            f"Explain why ETF {move.ticker} {direction} {pct} over {move.window_label}: "
            "sector rotation, rate expectations, or top-holding spillover — not company filings."
        )
        seeds = [
            "Sector / factor rotation drove the ETF print",
            "Moved with the broad market (beta)",
            "Top holdings news spilled into the ETF",
            "Rates / macro expectations shifted the basket",
        ]
    else:
        tools = ["get_stock_news", "get_recent_filings", "get_stock_price"]
        if move.window_label not in ("1d", "intraday"):
            tools.append("get_price_history")
        need_ir = browser_mcp_enabled() and (
            abs_move >= 2.5
            or (move.move_pct or 0) <= -2.0
            or depth == "deep"
        )
        if need_ir:
            tools.append("get_ir_materials")
            if abs_move >= 4.0 or depth == "deep":
                tools.append("get_shareholder_letter")
        focus = (
            f"Explain why {move.ticker} {direction} {pct} over {move.window_label}: "
            "company-specific catalysts vs sector/market beta."
        )
        seeds = [
            f"Company-specific news or guidance drove the {direction} move",
            "Sector or market beta; little idiosyncratic signal",
            "Filing / earnings / IR disclosure changed expectations",
            "Liquidity or positioning (no clear fundamental catalyst)",
        ]

    return {
        "focus": focus,
        "tools": tools,
        "seed_hypotheses": seeds,
        "skip_reason": (
            "Move below noise threshold"
            if move.is_noise
            else None
        ),
        "need_browser_ir": need_ir,
    }

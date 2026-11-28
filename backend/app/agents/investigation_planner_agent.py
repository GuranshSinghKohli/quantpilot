"""PRD v3 Phase 8/10 — Investigation Planner: what evidence to gather for this move."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.models.investigation_plan_schemas import ResearchPlan, ResearchTask
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

_TOOL_CATEGORIES = {
    "get_stock_news": "news",
    "get_recent_filings": "filings",
    "get_stock_price": "price",
    "get_price_history": "price",
    "get_stock_fundamentals": "fundamentals",
    "get_ir_materials": "primary_source",
    "get_shareholder_letter": "primary_source",
    "browser_find_document": "primary_source",
    "browser_search": "primary_source",
    "browser_open": "primary_source",
    "browser_download": "primary_source",
    "browser_extract": "primary_source",
}

_TOOL_OBJECTIVES = {
    "get_stock_news": "Search recent company and industry headlines.",
    "get_recent_filings": "Retrieve latest material SEC filings.",
    "get_stock_price": "Measure the price move versus the prior close.",
    "get_price_history": "Compare the move to recent realized volatility.",
    "get_stock_fundamentals": "Check earnings/valuation context and upcoming prints.",
    "get_ir_materials": "Locate official investor-relations materials.",
    "get_shareholder_letter": "Find a shareholder / annual letter if published.",
    "browser_find_document": "Find a specific official IR document when APIs are insufficient.",
    "browser_search": "Search an official company or government domain.",
    "browser_open": "Open one official primary-source URL.",
    "browser_download": "Download an official document or PDF.",
    "browser_extract": "Extract a relevant excerpt from retrieved official text.",
}

logger = get_logger("investigation_planner")

VALID_TOOLS = {
    "get_stock_news",
    "get_recent_filings",
    "get_stock_price",
    "get_price_history",
    "get_ir_materials",
    "get_shareholder_letter",
    "get_stock_fundamentals",
    "browser_find_document",
    "browser_search",
    "browser_open",
    "browser_download",
    "browser_extract",
}

BROWSER_TOOLS = {
    "get_ir_materials",
    "get_shareholder_letter",
    "browser_find_document",
    "browser_search",
    "browser_open",
    "browser_download",
    "browser_extract",
}
_BROWSER_CONTRACT_TOOLS = {
    "browser_find_document",
    "browser_search",
    "browser_open",
    "browser_download",
    "browser_extract",
}


def _lightweight_env() -> bool:
    return os.getenv("QUANTPILOT_LIGHTWEIGHT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def browser_mcp_enabled() -> bool:
    return os.getenv("BROWSER_MCP_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _safe_memory_payload(company_memory: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Strip portfolio / user-private fields before the planner sees memory."""
    if not isinstance(company_memory, dict):
        return {}
    blocked = {"portfolio_exposure", "portfolio", "holdings", "owner_key", "user_id"}
    return {k: v for k, v in company_memory.items() if k not in blocked}


async def plan_investigation(
    move: MoveSnapshot,
    *,
    asset_hint: str = "equity",
    event: Optional[Dict[str, Any]] = None,
    materiality_score: float = 0.0,
    portfolio_exposure: float = 0.0,
    company_memory: Optional[Dict[str, Any]] = None,
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
        "event": event or {},
        "materiality_score": materiality_score,
        "portfolio_exposure": portfolio_exposure,
        "company_memory": _safe_memory_payload(company_memory),
        "available_tools": sorted(
            t
            for t in VALID_TOOLS
            if t not in _BROWSER_CONTRACT_TOOLS
            and (t not in BROWSER_TOOLS or browser_mcp_enabled())
        ),
    }

    available = sorted(
        t
        for t in VALID_TOOLS
        if t not in _BROWSER_CONTRACT_TOOLS
        and (t not in BROWSER_TOOLS or browser_mcp_enabled())
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
            "(need_browser_ir=true). Do NOT choose browser_* or OpenClaw for prices, "
            "filings, or news — those use structured MCP APIs. seed_hypotheses: 3-5 "
            "short competing explanations. "
            "company_memory is historical context for this ticker only. "
            "Ask whether today's event is consistent with prior themes. "
            "Do not treat memory as current evidence. New evidence always wins."
        ),
        user_prompt=f"Plan investigation:\n{json.dumps(payload, default=str)}",
        fallback=fallback,
    )

    tools = [
        t
        for t in (result.get("tools") or [])
        if isinstance(t, str) and t in VALID_TOOLS and t not in _BROWSER_CONTRACT_TOOLS
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
        "company_memory": _safe_memory_payload(company_memory),
    }
    from app.agents.hypothesis_generator import catalog_hypotheses, generate_hypotheses

    if _lightweight_env():
        records = catalog_hypotheses(
            move, asset_hint=hint, event=event, seeds=hypotheses
        )
    else:
        records = await generate_hypotheses(
            move,
            asset_hint=hint,
            event=event,
            seeds=hypotheses,
            materiality_score=materiality_score,
            portfolio_exposure=portfolio_exposure,
        )
    plan["hypothesis_records"] = [r.model_dump() for r in records]
    plan["event"] = event or {}
    if records:
        plan["seed_hypotheses"] = [r.title for r in records]
        hypotheses = plan["seed_hypotheses"]
    structured = research_plan_from_legacy(plan, move=move, asset_hint=hint)
    plan["research_plan"] = structured.model_dump()
    plan["tasks"] = [t.model_dump() for t in structured.tasks]
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


def research_plan_from_legacy(
    plan: Dict[str, Any],
    *,
    move: Optional[MoveSnapshot] = None,
    asset_hint: str = "equity",
) -> ResearchPlan:
    """Turn the existing planner dict into a structured ResearchPlan."""
    tools = [
        t
        for t in (plan.get("tools") or [])
        if isinstance(t, str) and t in VALID_TOOLS
    ]
    hypotheses = [
        h.strip()
        for h in (plan.get("seed_hypotheses") or [])
        if isinstance(h, str) and h.strip()
    ]
    if not hypotheses:
        hypotheses = [
            "Company-specific catalyst",
            "Market / sector beta",
            "Unknown or liquidity-driven move",
        ]
    if not tools:
        tools = ["get_stock_news", "get_stock_price"]

    focus = str(plan.get("focus") or "")
    question = focus or (
        f"Why did {getattr(move, 'ticker', 'the ticker')} move?"
        if move is not None
        else "Why did this ticker move?"
    )
    tasks = tasks_from_tools(
        tools,
        hypothesis_count=len(hypotheses),
        hypothesis_records=plan.get("hypothesis_records") or [],
    )
    expected = sorted({_TOOL_CATEGORIES.get(t, "other") for t in tools})
    return ResearchPlan(
        investigation_question=question[:500],
        hypotheses=hypotheses[:5],
        tasks=tasks,
        required_evidence=expected,
        priority=str(getattr(move, "depth", None) or plan.get("priority") or "standard"),
        expected_sources=expected,
        stopping_conditions=[
            "At least one non-price evidence item, or two news items, or one filing.",
            "Stop after MAX_INVESTIGATION_ITERATIONS even if gaps remain.",
        ],
        tools=tools,
        need_browser_ir=bool(plan.get("need_browser_ir")),
        focus=focus[:500],
    )


def tasks_from_tools(
    tools: List[str],
    *,
    hypothesis_count: int = 0,
    start_index: int = 1,
    hypothesis_records: Optional[List[Dict[str, Any]]] = None,
) -> List[ResearchTask]:
    default_ids = [f"H{i}" for i in range(1, max(1, hypothesis_count) + 1)]
    tasks: List[ResearchTask] = []
    for offset, tool in enumerate(tools):
        if tool not in VALID_TOOLS:
            continue
        category = _TOOL_CATEGORIES.get(tool, "other")
        hyp_ids = _hypothesis_ids_for_tool(
            category, hypothesis_records, default_ids
        )
        tasks.append(
            ResearchTask(
                task_id=f"T{start_index + offset}",
                category=category,  # type: ignore[arg-type]
                objective=_TOOL_OBJECTIVES.get(tool, f"Call {tool}."),
                hypothesis_ids=hyp_ids,
                required_tool=tool,
                priority=offset + 1,
                status="pending",
            )
        )
    return tasks


def _hypothesis_ids_for_tool(
    category: str,
    records: Optional[List[Dict[str, Any]]],
    default_ids: List[str],
) -> List[str]:
    if not records:
        return default_ids
    matched = []
    for row in records:
        hid = str(row.get("hypothesis_id") or "")
        cats = [str(c).lower() for c in (row.get("tool_categories") or [])]
        if hid and (category in cats or not cats):
            matched.append(hid)
    return matched or default_ids


def validate_research_plan(plan: Dict[str, Any]) -> ResearchPlan:
    """Accept a raw plan dict; raise ValueError if hypotheses or tasks are missing."""
    structured = plan.get("research_plan") if isinstance(plan.get("research_plan"), dict) else plan
    parsed = ResearchPlan.model_validate(structured)
    if not parsed.hypotheses:
        raise ValueError("Research plan is missing hypotheses.")
    if not parsed.tasks and not parsed.tools:
        raise ValueError("Research plan is missing research tasks.")
    if not parsed.tasks:
        parsed.tasks = tasks_from_tools(parsed.tools, hypothesis_count=len(parsed.hypotheses))
    return parsed

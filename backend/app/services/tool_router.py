"""Phase 6 — deterministic MCP tool routing.

The LLM does not choose OpenClaw. Structured APIs win whenever they can
satisfy the requirement. Browser MCP is only for primary-source documents
that APIs do not provide.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional, Sequence, Set

from app.agents.investigation_planner_agent import VALID_TOOLS, browser_mcp_enabled

API_TOOLS = {
    "get_stock_news",
    "get_recent_filings",
    "get_stock_price",
    "get_price_history",
    "get_stock_fundamentals",
}

PRIMARY_SOURCE_API_TOOLS = {
    "get_ir_materials",
    "get_shareholder_letter",
}

BROWSER_CONTRACT_TOOLS = {
    "browser_search",
    "browser_open",
    "browser_find_document",
    "browser_download",
    "browser_extract",
}

_KIND_TO_API = {
    "FINANCIAL_DATA": "get_stock_price",
    "PRICE": "get_stock_price",
    "FUNDAMENTALS": "get_stock_fundamentals",
    "SEC_FILING": "get_recent_filings",
    "FILINGS": "get_recent_filings",
    "NEWS": "get_stock_news",
    "ECONOMIC_DATA": "get_stock_price",
    "MACRO": "get_stock_price",
    "PRIMARY_SOURCE": "get_ir_materials",
    "PRIMARY_SOURCE_DOCUMENT": "browser_find_document",
}


def max_browser_tasks() -> int:
    try:
        return max(0, min(6, int(os.getenv("MAX_BROWSER_TASKS_PER_INVESTIGATION", "2"))))
    except (TypeError, ValueError):
        return 2


def is_browser_tool(tool: str) -> bool:
    """Expensive Browser MCP contract tools (not httpx IR helpers)."""
    return tool in BROWSER_CONTRACT_TOOLS


def route_requirement(
    kind: str,
    *,
    used_tools: Optional[Iterable[str]] = None,
    has_presentation: bool = False,
    browser_enabled: Optional[bool] = None,
) -> str:
    """Return the single MCP tool that should satisfy this requirement."""
    key = (kind or "").upper().strip()
    used = {str(t) for t in (used_tools or [])}
    enabled = browser_mcp_enabled() if browser_enabled is None else browser_enabled

    if key in {"FINANCIAL_DATA", "PRICE", "FUNDAMENTALS", "NEWS", "SEC_FILING", "FILINGS", "ECONOMIC_DATA", "MACRO"}:
        return _KIND_TO_API[key]

    if key in {"PRIMARY_SOURCE", "IR"}:
        return "get_ir_materials"

    if key in {"PRIMARY_SOURCE_DOCUMENT", "PRESENTATION", "PDF"}:
        if "get_ir_materials" not in used and not has_presentation:
            return "get_ir_materials"
        if enabled and "browser_find_document" not in used:
            return "browser_find_document"
        return "get_ir_materials"

    return _KIND_TO_API.get(key, "get_stock_news")


def should_use_browser(
    *,
    kind: str = "",
    used_tools: Optional[Iterable[str]] = None,
    has_presentation: bool = False,
    api_available: bool = True,
    browser_enabled: Optional[bool] = None,
) -> bool:
    """True only when a structured API cannot satisfy a primary-source need."""
    enabled = browser_mcp_enabled() if browser_enabled is None else browser_enabled
    if not enabled:
        return False
    key = (kind or "").upper()
    if key in {
        "FINANCIAL_DATA",
        "PRICE",
        "FUNDAMENTALS",
        "NEWS",
        "SEC_FILING",
        "FILINGS",
        "ECONOMIC_DATA",
        "MACRO",
    }:
        return False
    if has_presentation:
        return False
    used = {str(t) for t in (used_tools or [])}
    if key in {"PRIMARY_SOURCE_DOCUMENT", "PRESENTATION", "PDF"}:
        return "get_ir_materials" in used or not api_available
    return False


def filter_browser_budget(
    tools: Sequence[str],
    *,
    already_used: int = 0,
    seen_keys: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Cap expensive browser work and drop duplicate browser tools."""
    cap = max_browser_tasks()
    seen = set(seen_keys or [])
    kept: list = []
    skipped: list = []
    used = already_used
    for tool in tools:
        if tool not in VALID_TOOLS and tool not in BROWSER_CONTRACT_TOOLS:
            skipped.append(tool)
            continue
        if not is_browser_tool(tool):
            kept.append(tool)
            continue
        key = tool
        if key in seen:
            skipped.append(tool)
            continue
        if used >= cap:
            skipped.append(tool)
            continue
        seen.add(key)
        used += 1
        kept.append(tool)
    return {
        "tools": kept,
        "skipped": skipped,
        "browser_tasks_used": used,
        "seen": seen,
    }


def presentation_needed(hypotheses: Sequence[Any]) -> bool:
    tokens = (
        "investor presentation",
        "earnings presentation",
        "official presentation",
        "primary-source document",
    )
    for row in hypotheses or []:
        if isinstance(row, dict):
            blob = " ".join(
                str(row.get(key) or "")
                for key in ("title", "required_evidence", "missing_evidence", "statement")
            )
            if isinstance(row.get("required_evidence"), list):
                blob += " " + " ".join(str(x) for x in row["required_evidence"])
        else:
            blob = str(row)
        low = blob.lower()
        if any(token in low for token in tokens):
            return True
    return False


def has_official_presentation(evidence: Sequence[Dict[str, Any]]) -> bool:
    tokens = ("presentation", "slides", "deck", ".pdf")
    for item in evidence or []:
        if str(item.get("source_type") or "") not in {"ir_page", "filing"}:
            continue
        if (item.get("raw_payload") or {}).get("error"):
            continue
        blob = f"{item.get('title') or ''} {item.get('source_url') or ''} {item.get('excerpt') or ''}".lower()
        if any(token in blob for token in tokens):
            return True
    return False

"""Tool-efficiency proxies from recorded traces."""

from __future__ import annotations

from typing import Sequence

from evals.schemas import ToolCallFixture


def tool_metrics(trace: Sequence[ToolCallFixture], *, used_browser: bool, should_use_browser: bool | None) -> dict:
    total = len(trace)
    browser = sum(1 for t in trace if "browser" in t.tool or t.tool == "browser_find_document")
    mcp = sum(1 for t in trace if t.tool.startswith("get_") or t.tool.startswith("mcp"))
    failed = sum(1 for t in trace if not t.ok)
    dup = sum(1 for t in trace if t.duplicate)
    routing = 1.0
    if should_use_browser is False and used_browser:
        routing = 0.0
    if should_use_browser is True and not used_browser:
        routing = 0.0
    return {
        "tool_calls": float(total),
        "browser_calls": float(browser),
        "mcp_calls": float(mcp),
        "failed_tool_calls": float(failed),
        "duplicate_tool_call_rate": dup / total if total else 0.0,
        "tool_routing_accuracy": routing,
    }

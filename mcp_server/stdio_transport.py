"""Minimal MCP-compatible stdio JSON-RPC transport (Python 3.9+ fallback)."""

import json
import sys
from typing import Any, Callable, Dict, List


def _tool_def(name: str, description: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
    }


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    _tool_def(
        "get_stock_price",
        "Fetch current stock price, previous close, change %, and volume.",
        {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    ),
    _tool_def(
        "get_stock_fundamentals",
        "Fetch market cap, P/E, EPS, dividend yield, 52w range, sector, industry.",
        {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    ),
    _tool_def(
        "get_stock_news",
        "Fetch recent news headlines for a ticker.",
        {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["ticker"],
        },
    ),
    _tool_def(
        "get_price_history",
        "Fetch OHLCV price history for a ticker.",
        {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "default": "1mo"},
            },
            "required": ["ticker"],
        },
    ),
    _tool_def(
        "get_recent_filings",
        "Fetch recent SEC EDGAR filings for a ticker.",
        {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["ticker"],
        },
    ),
    _tool_def(
        "get_company_facts",
        "Fetch SEC company facts (CIK, name, SIC, state).",
        {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    ),
]


def run_stdio_server(handlers: Dict[str, Callable[..., Any]]) -> None:
    """Run newline-delimited JSON-RPC MCP-style server on stdin/stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "quantpilot-finance", "version": "1.0.0"},
                }
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                result = {"tools": TOOL_DEFINITIONS}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name not in handlers:
                    raise ValueError(f"Unknown tool: {name}")
                payload = handlers[name](**arguments)
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                    "isError": False,
                }
            else:
                raise ValueError(f"Unsupported method: {method}")

            if msg_id is not None:
                response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            if msg_id is not None:
                error = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
                sys.stdout.write(json.dumps(error) + "\n")
                sys.stdout.flush()

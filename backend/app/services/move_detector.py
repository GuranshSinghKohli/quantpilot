"""PRD v3 Phase 8/11 — detect whether a ticker move is worth investigating."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.mcp_client import MCPClientError, call_tool
from app.observability.logger import get_logger, log_event

logger = get_logger("move_detector")

# Absolute daily move below this can be treated as noise for proactive skips.
NOISE_PCT = float(os.getenv("INVESTIGATION_NOISE_PCT", "1.5"))


@dataclass
class MoveSnapshot:
    ticker: str
    move_pct: Optional[float]
    window_label: str
    current_price: Optional[float]
    previous_close: Optional[float]
    is_noise: bool
    source: str
    detail: str = ""
    # Phase 11 enrichment (optional)
    asset_class: str = "equity"
    realized_vol_pct: Optional[float] = None
    move_zscore: Optional[float] = None
    benchmark_ticker: str = "SPY"
    benchmark_move_pct: Optional[float] = None
    residual_pct: Optional[float] = None
    depth: str = "standard"
    should_investigate: bool = True
    skip_reason: str = ""


def noise_threshold() -> float:
    return NOISE_PCT


def as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def detect_move(ticker: str, window: str = "1d") -> MoveSnapshot:
    """Resolve recent move % via MCP price tools (history for multi-day windows)."""
    symbol = ticker.upper().strip()
    window_label = (window or "1d").strip().lower() or "1d"

    price = await safe_tool("get_stock_price", {"ticker": symbol})
    move_pct: Optional[float] = None
    current = None
    previous = None
    source = "none"

    if price:
        current = as_float(price.get("current_price"))
        previous = as_float(price.get("previous_close"))
        move_pct = as_float(price.get("price_change_percent"))
        source = "mcp:get_stock_price"

    if window_label not in ("1d", "intraday") or move_pct is None:
        history = await safe_tool(
            "get_price_history",
            {"ticker": symbol, "period": _period_for_window(window_label)},
        )
        hist_move = move_from_history(history.get("history") if history else None)
        if hist_move is not None:
            move_pct = hist_move
            source = "mcp:get_price_history"
            if history and history.get("history"):
                rows = history["history"]
                if rows:
                    current = current or as_float(rows[-1].get("close"))
                    if len(rows) >= 2:
                        previous = previous or as_float(rows[0].get("close"))

    if move_pct is None:
        try:
            from mcp_server.tools import stock_tools

            local = stock_tools.get_stock_price(symbol)
            move_pct = as_float(local.get("price_change_percent"))
            current = current or as_float(local.get("current_price"))
            previous = previous or as_float(local.get("previous_close"))
            source = "yfinance_direct"
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "Move detection failed",
                ticker=symbol,
                error=str(exc),
            )
            return MoveSnapshot(
                ticker=symbol,
                move_pct=None,
                window_label=window_label,
                current_price=current,
                previous_close=previous,
                is_noise=False,
                source=source,
                detail=f"Unable to resolve move: {exc}",
            )

    abs_move = abs(move_pct) if move_pct is not None else 0.0
    is_noise = move_pct is not None and abs_move < NOISE_PCT
    direction = "up" if (move_pct or 0) >= 0 else "down"
    detail = (
        f"{symbol} {direction} {abs_move:.2f}% over {window_label}"
        f" (noise threshold {NOISE_PCT:.1f}%)."
    )
    return MoveSnapshot(
        ticker=symbol,
        move_pct=round(move_pct, 4) if move_pct is not None else None,
        window_label=window_label,
        current_price=current,
        previous_close=previous,
        is_noise=is_noise,
        source=source,
        detail=detail,
    )


def _period_for_window(window_label: str) -> str:
    """Map investigation window → yfinance history period covering that move."""
    mapping = {
        "1d": "5d",
        "intraday": "5d",
        "5d": "5d",
        "1w": "5d",
        "1mo": "1mo",
        "3mo": "3mo",
        "1y": "1y",
        "ytd": "ytd",
    }
    return mapping.get(window_label, "1mo")


def move_from_history(history: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    if not history or len(history) < 2:
        return None
    first = as_float(history[0].get("close"))
    last = as_float(history[-1].get("close"))
    if first is None or last is None or first == 0:
        return None
    return ((last - first) / first) * 100.0


async def safe_tool(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        result = await call_tool(name, arguments)
        return result if isinstance(result, dict) else None
    except MCPClientError as exc:
        log_event(
            logger,
            logging.WARNING,
            "MCP tool failed in move detector",
            tool=name,
            error=str(exc),
        )
        return None
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Unexpected MCP failure in move detector",
            tool=name,
            error=str(exc),
        )
        return None


# Back-compat aliases used by older imports/tests
_as_float = as_float
_safe_tool = safe_tool
_move_from_history = move_from_history

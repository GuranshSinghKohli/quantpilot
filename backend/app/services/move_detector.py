"""PRD v3 Phase 8/11 — detect whether a ticker move is worth investigating."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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


def _needs_history(window_label: str) -> bool:
    return window_label not in ("1d", "intraday")


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
        if not _needs_history(window_label):
            move_pct = as_float(price.get("price_change_percent"))
            source = "mcp:get_stock_price"
        else:
            source = "mcp:get_stock_price(quote_only)"

    # Multi-day windows must use history — never leave a 1d % as the answer.
    if _needs_history(window_label) or move_pct is None:
        history = await safe_tool(
            "get_price_history",
            {"ticker": symbol, "period": _period_for_window(window_label)},
        )
        hist_move, cur, prev = _history_move_and_bounds(
            history.get("history") if history else None
        )
        if hist_move is not None:
            move_pct = hist_move
            source = "mcp:get_price_history"
            current = current or cur
            previous = previous or prev

    # Local yfinance fallback (MCP down / empty history / broken tool module).
    needs_local = move_pct is None or (
        _needs_history(window_label) and "price_history" not in source
    )
    if needs_local:
        local_move, local_source, cur, prev = _local_yfinance_move(
            symbol, window_label
        )
        if local_move is not None:
            move_pct = local_move
            source = local_source
            current = current or cur
            previous = previous or prev

    if move_pct is None and not _needs_history(window_label):
        # Last resort for 1d: quote change % from local price tool.
        try:
            from mcp_server.tools import stock_tools

            local = stock_tools.get_stock_price(symbol)
            move_pct = as_float(local.get("price_change_percent"))
            current = current or as_float(local.get("current_price"))
            previous = previous or as_float(local.get("previous_close"))
            if move_pct is not None:
                source = "yfinance_direct:get_stock_price"
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "Move detection failed",
                ticker=symbol,
                error=str(exc),
            )

    return _snapshot(
        symbol=symbol,
        window_label=window_label,
        move_pct=move_pct,
        current=current,
        previous=previous,
        source=source,
    )


def _local_yfinance_move(
    symbol: str, window_label: str
) -> Tuple[Optional[float], str, Optional[float], Optional[float]]:
    try:
        from mcp_server.tools import stock_tools

        period = _period_for_window(window_label)
        history = stock_tools.get_price_history(symbol, period=period)
        hist_move, cur, prev = _history_move_and_bounds(
            history.get("history") if history else None
        )
        if hist_move is not None:
            return hist_move, "yfinance_direct:get_price_history", cur, prev
        if not _needs_history(window_label):
            local = stock_tools.get_stock_price(symbol)
            return (
                as_float(local.get("price_change_percent")),
                "yfinance_direct:get_stock_price",
                as_float(local.get("current_price")),
                as_float(local.get("previous_close")),
            )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Local yfinance move fallback failed",
            ticker=symbol,
            window=window_label,
            error=str(exc),
        )
    return None, "none", None, None


def _history_move_and_bounds(
    history: Optional[List[Dict[str, Any]]],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not history or len(history) < 2:
        return None, None, None
    first = as_float(history[0].get("close"))
    last = as_float(history[-1].get("close"))
    if first is None or last is None or first == 0:
        return None, last, first
    return ((last - first) / first) * 100.0, last, first


def _snapshot(
    *,
    symbol: str,
    window_label: str,
    move_pct: Optional[float],
    current: Optional[float],
    previous: Optional[float],
    source: str,
) -> MoveSnapshot:
    abs_move = abs(move_pct) if move_pct is not None else 0.0
    is_noise = move_pct is not None and abs_move < NOISE_PCT
    window_human = {
        "1d": "1 day",
        "intraday": "1 day",
        "1w": "1 week",
        "5d": "1 week",
        "1mo": "1 month",
        "3mo": "3 months",
        "6mo": "6 months",
        "1y": "1 year",
        "ytd": "year to date",
    }.get(window_label, window_label)
    if move_pct is None:
        detail = f"{symbol} move over {window_human} could not be measured."
    elif move_pct < -0.05:
        detail = (
            f"{symbol} fell {abs_move:.2f}% over {window_human}"
            f" (noise threshold {NOISE_PCT:.1f}%)."
        )
    elif move_pct > 0.05:
        detail = (
            f"{symbol} rose {abs_move:.2f}% over {window_human}"
            f" (noise threshold {NOISE_PCT:.1f}%)."
        )
    else:
        detail = (
            f"{symbol} was roughly flat ({move_pct:+.2f}%) over {window_human}"
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
        "6mo": "6mo",
        "1y": "1y",
        "ytd": "ytd",
    }
    return mapping.get(window_label, "1mo")


def move_from_history(history: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    move, _, _ = _history_move_and_bounds(history)
    return move


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

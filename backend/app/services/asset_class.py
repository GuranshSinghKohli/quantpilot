"""PRD v3 Phase 11 — equity vs ETF classification for evidence paths."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.observability.logger import get_logger, log_event

logger = get_logger("asset_class")

# Common US ETF tickers as a fast path when Yahoo quoteType is unavailable.
_KNOWN_ETFS = {
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VOO",
    "VTI",
    "VEA",
    "VWO",
    "EFA",
    "EEM",
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "ARKK",
    "TQQQ",
    "SQQQ",
    "GLD",
    "SLV",
    "TLT",
    "HYG",
    "LQD",
}


def classify_asset(ticker: str) -> str:
    """Return 'etf' | 'equity' | 'other'."""
    symbol = (ticker or "").upper().strip()
    if not symbol:
        return "equity"
    if symbol in _KNOWN_ETFS:
        return "etf"

    info = _yahoo_info(symbol)
    if not info:
        return "equity"

    quote_type = str(
        info.get("quoteType")
        or info.get("quote_type")
        or info.get("typeDisp")
        or ""
    ).upper()
    category = str(info.get("category") or "").lower()
    name = str(info.get("longName") or info.get("shortName") or "").lower()

    if quote_type in {"ETF", "MUTUALFUND", "INDEX"}:
        return "etf" if quote_type == "ETF" else "other"
    if "etf" in category or "etf" in name or "exchange traded" in name:
        return "etf"
    if quote_type in {"EQUITY", "STOCK"}:
        return "equity"
    return "equity"


def _yahoo_info(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        import yfinance as yf

        return yf.Ticker(symbol).info or {}
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Asset class lookup failed",
            ticker=symbol,
            error=str(exc),
        )
        return None

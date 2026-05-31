import os
from typing import Any, Dict, Optional, Tuple

import httpx
import yfinance as yf

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _sec_headers() -> Dict[str, str]:
    user_agent = os.getenv(
        "SEC_EDGAR_USER_AGENT",
        "QuantPilot/1.0 (contact@example.com)",
    )
    return {"User-Agent": user_agent, "Accept": "application/json"}


def safe_get(data: Dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(data, dict):
        return default
    return data.get(key, default)


def format_market_cap(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if num >= 1_000_000_000_000:
        return f"${num / 1_000_000_000_000:.2f}T"
    if num >= 1_000_000_000:
        return f"${num / 1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"${num / 1_000_000:.2f}M"
    return f"${num:,.0f}"


def validate_ticker(ticker: str) -> str:
    symbol = ticker.upper().strip()
    if not symbol or len(symbol) > 5 or not symbol.isalnum():
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    stock = yf.Ticker(symbol)
    info = stock.info or {}
    if not info.get("symbol") and not info.get("regularMarketPrice"):
        raise ValueError(f"Ticker '{symbol}' not found in Yahoo Finance.")
    return symbol


def ticker_to_cik(ticker: str) -> Tuple[str, Optional[str]]:
    symbol = validate_ticker(ticker)
    with httpx.Client(timeout=30.0) as client:
        response = client.get(SEC_TICKERS_URL, headers=_sec_headers())
        response.raise_for_status()
        data = response.json()
    for entry in data.values():
        if entry.get("ticker", "").upper() == symbol:
            cik = str(entry["cik_str"]).zfill(10)
            return cik, entry.get("title")
    raise ValueError(f"No SEC CIK found for ticker '{symbol}'")

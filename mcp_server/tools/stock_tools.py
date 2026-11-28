from typing import Any, Dict, List

import yfinance as yf

from mcp_server.tools.utils import safe_get, validate_ticker


def get_stock_price(ticker: str) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    info = yf.Ticker(symbol).info or {}
    current = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice")
    previous = safe_get(info, "previousClose")
    change_pct = safe_get(info, "regularMarketChangePercent")
    if change_pct is None and current and previous:
        change_pct = ((current - previous) / previous) * 100
    return {
        "ticker": symbol,
        "current_price": current,
        "previous_close": previous,
        "price_change_percent": change_pct,
        "volume": safe_get(info, "volume") or safe_get(info, "regularMarketVolume"),
    }


def _earnings_fields(info: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort earnings date from yfinance info (unix ts or date list)."""
    raw = (
        safe_get(info, "earningsTimestamp")
        or safe_get(info, "earningsTimestampStart")
        or safe_get(info, "earningsDate")
    )
    if isinstance(raw, (list, tuple)) and raw:
        raw = raw[0]
    timestamp = None
    iso = None
    if raw is not None:
        from datetime import datetime, timezone

        if hasattr(raw, "timestamp"):
            try:
                timestamp = float(raw.timestamp())
                iso = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                timestamp = None
        if iso is None:
            try:
                ts = float(raw)
                if ts > 1e12:
                    ts /= 1000.0
                timestamp = ts
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OverflowError, OSError):
                text = str(raw)
                iso = text[:10] if len(text) >= 10 else text
    surprise = safe_get(info, "earningsQuarterlyGrowth")
    if surprise is not None:
        try:
            surprise = float(surprise) * 100.0
        except (TypeError, ValueError):
            surprise = None
    return {
        "earnings_date": iso,
        "earnings_timestamp": timestamp,
        "next_earnings_date": iso,
        "earnings_surprise_pct": surprise,
    }


def get_stock_fundamentals(ticker: str) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    info = yf.Ticker(symbol).info or {}
    payload = {
        "ticker": symbol,
        "market_cap": safe_get(info, "marketCap"),
        "pe_ratio": safe_get(info, "trailingPE"),
        "eps": safe_get(info, "trailingEps"),
        "dividend_yield": safe_get(info, "dividendYield"),
        "fifty_two_week_high": safe_get(info, "fiftyTwoWeekHigh"),
        "fifty_two_week_low": safe_get(info, "fiftyTwoWeekLow"),
        "sector": safe_get(info, "sector", ""),
        "industry": safe_get(info, "industry", ""),
    }
    payload.update(_earnings_fields(info))
    return payload


def _news_link(value: Any) -> str:
    """Normalize yfinance news URL fields (string or {url: ...})."""
    if isinstance(value, dict):
        return str(value.get("url") or value.get("href") or "")[:1024]
    if value:
        return str(value)[:1024]
    return ""


def get_stock_news(ticker: str, limit: int = 5) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    news = yf.Ticker(symbol).news or []
    headlines: List[Dict[str, Any]] = []
    for item in news[: max(1, min(limit, 20))]:
        content = item.get("content") or item
        link = (
            _news_link(content.get("canonicalUrl"))
            or _news_link(content.get("clickThroughUrl"))
            or _news_link(item.get("link"))
            or _news_link(content.get("link"))
        )
        headlines.append(
            {
                "title": content.get("title") or item.get("title", ""),
                "publisher": content.get("publisher") or item.get("publisher", ""),
                "link": link,
                "published": content.get("pubDate") or item.get("providerPublishTime"),
            }
        )
    return {"ticker": symbol, "headlines": headlines}


def get_price_history(ticker: str, period: str = "1mo") -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    hist = yf.Ticker(symbol).history(period=period)
    rows: List[Dict[str, Any]] = []
    for idx, row in hist.iterrows():
        rows.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            }
        )
    return {"ticker": symbol, "period": period, "history": rows}

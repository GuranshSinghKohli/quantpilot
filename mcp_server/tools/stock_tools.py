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


def get_stock_fundamentals(ticker: str) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    info = yf.Ticker(symbol).info or {}
    return {
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


def get_stock_news(ticker: str, limit: int = 5) -> Dict[str, Any]:
    symbol = validate_ticker(ticker)
    news = yf.Ticker(symbol).news or []
    headlines: List[Dict[str, Any]] = []
    for item in news[: max(1, min(limit, 20))]:
        content = item.get("content") or item
        headlines.append(
            {
                "title": content.get("title") or item.get("title", ""),
                "publisher": content.get("publisher") or item.get("publisher", ""),
                "link": content.get("canonicalUrl")
                or content.get("clickThroughUrl")
                or item.get("link", ""),
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

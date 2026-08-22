import asyncio

import yfinance as yf

from app.models.schemas import StockResponse


def _fetch_stock_sync(ticker: str) -> StockResponse:
    symbol = ticker.upper().strip()
    stock = yf.Ticker(symbol)
    info = stock.info

    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        if not info.get("symbol"):
            raise ValueError(f"No data found for ticker '{symbol}'")

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")

    return StockResponse(
        ticker=info.get("symbol", symbol),
        current_price=current_price,
        previous_close=info.get("previousClose"),
        market_cap=info.get("marketCap"),
        pe_ratio=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        eps=info.get("trailingEps"),
        dividend_yield=info.get("dividendYield"),
        beta=info.get("beta"),
        volume=info.get("volume") or info.get("regularMarketVolume"),
        avg_volume=info.get("averageVolume"),
        day_high=info.get("dayHigh") or info.get("regularMarketDayHigh"),
        day_low=info.get("dayLow") or info.get("regularMarketDayLow"),
        fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
        fifty_two_week_low=info.get("fiftyTwoWeekLow"),
        sector=info.get("sector"),
        industry=info.get("industry"),
    )


async def get_stock_data(ticker: str) -> StockResponse:
    return await asyncio.to_thread(_fetch_stock_sync, ticker)


def _fetch_news_sync(ticker: str) -> list:
    symbol = ticker.upper().strip()
    stock = yf.Ticker(symbol)
    news = stock.news or []
    headlines = []
    for item in news[:10]:
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
    return headlines


async def get_ticker_news(ticker: str) -> list:
    return await asyncio.to_thread(_fetch_news_sync, ticker)

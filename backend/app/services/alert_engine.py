"""Smart alert evaluation (price / volatility / news sentiment)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.config import QUOTE_CACHE_TTL_SECONDS
from app.db.models import AlertRule
from app.db.session import SessionLocal
from app.models.alert_schemas import AlertEventOut
from app.observability.logger import get_logger, log_event
from app.services import alert_store, redis_store
from app.services.yahoo_finance import get_ticker_news

logger = get_logger("alert_engine")

_BULLISH = {
    "surge", "soar", "beat", "growth", "upgrade", "buy", "record",
    "strong", "rally", "gain", "profit", "outperform", "bullish",
}
_BEARISH = {
    "fall", "drop", "miss", "downgrade", "sell", "weak", "cut",
    "lawsuit", "decline", "loss", "bearish", "warning", "layoff",
}


def _news_score(headlines: List[Dict[str, Any]]) -> float:
    if not headlines:
        return 0.0
    score = 0
    for item in headlines:
        title = (item.get("title") or "").lower()
        for w in _BULLISH:
            if w in title:
                score += 1
        for w in _BEARISH:
            if w in title:
                score -= 1
    return max(-1.0, min(1.0, score / max(1, len(headlines))))


def _fetch_quote_sync(ticker: str) -> Dict[str, Any]:
    import yfinance as yf

    symbol = ticker.upper().strip()
    info = yf.Ticker(symbol).info or {}
    current = info.get("currentPrice") or info.get("regularMarketPrice")
    previous = info.get("previousClose")
    change_pct = info.get("regularMarketChangePercent")
    if change_pct is None and current and previous and previous != 0:
        change_pct = ((float(current) - float(previous)) / float(previous)) * 100
    return {
        "ticker": symbol,
        "current_price": float(current) if current is not None else None,
        "previous_close": float(previous) if previous is not None else None,
        "change_percent": float(change_pct) if change_pct is not None else None,
    }


async def get_quote_cached(ticker: str) -> Dict[str, Any]:
    key = redis_store.quote_cache_key(ticker)
    cached = redis_store.cache_get(key)
    if isinstance(cached, dict) and cached.get("ticker"):
        return cached
    quote = await asyncio.to_thread(_fetch_quote_sync, ticker)
    redis_store.cache_set(key, quote, ttl_seconds=QUOTE_CACHE_TTL_SECONDS)
    return quote


def _check_rule(
    rule: AlertRule,
    quote: Dict[str, Any],
    sentiment: Optional[float],
) -> Optional[Tuple[str, str, float]]:
    t = rule.alert_type
    threshold = float(rule.threshold)
    price = quote.get("current_price")
    change = quote.get("change_percent")

    if t == "price_above":
        if price is None:
            return None
        if float(price) >= threshold:
            return (
                f"{rule.ticker} above ${threshold:g}",
                (
                    f"{rule.ticker} traded at ${float(price):.2f}, "
                    f"at or above your ${threshold:g} threshold."
                ),
                float(price),
            )
    elif t == "price_below":
        if price is None:
            return None
        if float(price) <= threshold:
            return (
                f"{rule.ticker} below ${threshold:g}",
                (
                    f"{rule.ticker} traded at ${float(price):.2f}, "
                    f"at or below your ${threshold:g} threshold."
                ),
                float(price),
            )
    elif t == "volatility_pct":
        if change is None:
            return None
        if abs(float(change)) >= abs(threshold):
            return (
                f"{rule.ticker} moved {float(change):+.2f}%",
                (
                    f"{rule.ticker} daily change is {float(change):+.2f}%, "
                    f"crossing your {abs(threshold):g}% volatility threshold."
                ),
                float(change),
            )
    elif t == "news_sentiment":
        if sentiment is None:
            return None
        if threshold >= 0 and sentiment >= threshold:
            return (
                f"{rule.ticker} bullish news pulse",
                (
                    f"News sentiment score {sentiment:+.2f} met your bullish "
                    f"threshold of {threshold:g}."
                ),
                float(sentiment),
            )
        if threshold < 0 and sentiment <= threshold:
            return (
                f"{rule.ticker} bearish news pulse",
                (
                    f"News sentiment score {sentiment:+.2f} met your bearish "
                    f"threshold of {threshold:g}."
                ),
                float(sentiment),
            )
    return None


async def evaluate_all_enabled_rules(user_id: Optional[int] = None) -> Dict[str, Any]:
    from app.db.models import User

    db = SessionLocal()
    try:
        if user_id is not None:
            user = db.get(User, user_id)
            rules = [r for r in alert_store.list_rules(db, user)] if user else []
        else:
            rules = alert_store.list_enabled_rules(db)
        snapshots = [
            {
                "id": r.id,
                "ticker": r.ticker.upper(),
                "alert_type": r.alert_type,
            }
            for r in rules
            if r.enabled
        ]
    finally:
        db.close()

    if not snapshots:
        return {
            "evaluated_rules": 0,
            "triggered": 0,
            "redis_mode": redis_store.redis_mode(),
            "events": [],
        }

    tickers = sorted({s["ticker"] for s in snapshots})
    need_news = {s["ticker"] for s in snapshots if s["alert_type"] == "news_sentiment"}

    quotes: Dict[str, Dict[str, Any]] = {}
    sentiments: Dict[str, float] = {}

    for ticker in tickers:
        try:
            quotes[ticker] = await get_quote_cached(ticker)
        except Exception as exc:
            log_event(logger, logging.WARNING, "Quote fetch failed", ticker=ticker, error=str(exc))
            quotes[ticker] = {"ticker": ticker}

    for ticker in need_news:
        try:
            headlines = await get_ticker_news(ticker)
            sentiments[ticker] = _news_score(headlines[:8])
        except Exception as exc:
            log_event(logger, logging.WARNING, "News fetch failed", ticker=ticker, error=str(exc))
            sentiments[ticker] = 0.0

    triggered: List[AlertEventOut] = []
    db = SessionLocal()
    try:
        for snap in snapshots:
            rule = db.get(AlertRule, snap["id"])
            if rule is None or not rule.enabled:
                continue
            if alert_store.in_cooldown(rule):
                continue
            quote = quotes.get(rule.ticker.upper(), {})
            sentiment = sentiments.get(rule.ticker.upper())
            hit = _check_rule(rule, quote, sentiment)
            if hit is None:
                continue
            title, message, observed = hit
            event = alert_store.record_trigger(
                db,
                rule,
                title=title,
                message=message,
                observed_value=observed,
            )
            triggered.append(alert_store.event_to_out(event))
            log_event(
                logger,
                logging.INFO,
                "Alert triggered",
                rule_id=rule.id,
                ticker=rule.ticker,
                alert_type=rule.alert_type,
            )
    finally:
        db.close()

    return {
        "evaluated_rules": len(snapshots),
        "triggered": len(triggered),
        "redis_mode": redis_store.redis_mode(),
        "events": triggered,
    }

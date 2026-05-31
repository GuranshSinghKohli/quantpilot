import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from app.agents.llm import call_openai_json
from app.models.schemas import (
    RecommendationPick,
    RecommendationsResponse,
    StockResponse,
)
from app.services.yahoo_finance import get_stock_data, get_ticker_news

DEFAULT_SCAN_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "JPM",
]

DISCLAIMER = (
    "AI-generated screening for educational purposes only — not financial advice. "
    "Past patterns do not guarantee future returns. Always do your own research "
    "before investing."
)

BULLISH_WORDS = {
    "surge",
    "soar",
    "beat",
    "growth",
    "upgrade",
    "buy",
    "record",
    "strong",
    "rally",
    "gain",
    "profit",
    "outperform",
    "bullish",
}
BEARISH_WORDS = {
    "fall",
    "drop",
    "miss",
    "downgrade",
    "sell",
    "weak",
    "cut",
    "lawsuit",
    "decline",
    "loss",
    "bearish",
    "warning",
    "layoff",
}


def _news_sentiment(headlines: List[Dict[str, Any]]) -> Tuple[float, str]:
    if not headlines:
        return 0.0, "Limited recent news coverage"

    text = " ".join(
        (h.get("title") or "").lower() for h in headlines[:8]
    )
    bull = sum(1 for w in BULLISH_WORDS if w in text)
    bear = sum(1 for w in BEARISH_WORDS if w in text)

    if bull > bear + 1:
        return 0.12, f"News tone leans positive ({bull} bullish signals in headlines)"
    if bear > bull + 1:
        return -0.12, f"News tone leans cautious ({bear} bearish signals in headlines)"
    return 0.02, "Mixed or neutral news sentiment"


def _score_ticker(
    stock: StockResponse, headlines: List[Dict[str, Any]]
) -> Tuple[float, Literal["bullish", "neutral", "bearish"], List[str]]:
    score = 0.5
    reasons: List[str] = []

    price = stock.current_price
    low = stock.fifty_two_week_low
    high = stock.fifty_two_week_high

    if price and low and high and high > low:
        position = (price - low) / (high - low)
        if position < 0.35:
            score += 0.14
            reasons.append(
                "Price in lower third of 52-week range — room for near-term recovery"
            )
        elif position > 0.88:
            score -= 0.1
            reasons.append("Trading near 52-week high — less near-term upside headroom")
        else:
            score += 0.06
            reasons.append("Mid-range vs 52-week high/low — balanced momentum setup")

    if stock.pe_ratio is not None:
        pe = stock.pe_ratio
        if 0 < pe < 18:
            score += 0.1
            reasons.append(f"P/E near {pe:.1f} — relatively modest valuation")
        elif pe > 40:
            score -= 0.08
            reasons.append(f"P/E near {pe:.1f} — premium valuation")
        elif 18 <= pe <= 30:
            score += 0.04
            reasons.append(f"P/E near {pe:.1f} — fair-to-moderate valuation")

    news_delta, news_reason = _news_sentiment(headlines)
    score += news_delta
    reasons.append(news_reason)

    if not headlines:
        score -= 0.05
        reasons.append("Thin news flow lowers conviction")

    score = max(0.0, min(1.0, round(score, 3)))

    if score >= 0.58:
        outlook: Literal["bullish", "neutral", "bearish"] = "bullish"
    elif score <= 0.42:
        outlook = "bearish"
    else:
        outlook = "neutral"

    return score, outlook, reasons[:4]


async def _scan_one(ticker: str) -> Optional[RecommendationPick]:
    try:
        stock, headlines = await asyncio.gather(
            get_stock_data(ticker),
            get_ticker_news(ticker),
        )
        score, outlook, reasons = _score_ticker(stock, headlines)

        near_term = {
            "bullish": "Near-term outlook: constructive — signals favor potential upside",
            "neutral": "Near-term outlook: mixed — wait for clearer trend",
            "bearish": "Near-term outlook: cautious — headwinds in current data",
        }[outlook]

        return RecommendationPick(
            ticker=stock.ticker,
            score=score,
            outlook=outlook,
            near_term_view=near_term,
            reasons=reasons,
            current_price=stock.current_price,
        )
    except Exception:
        return None


async def _enrich_with_llm(picks: List[RecommendationPick]) -> List[RecommendationPick]:
    if not picks:
        return picks

    payload = [p.model_dump() for p in picks[:5]]
    fallback = {
        "picks": [
            {
                "ticker": p.ticker,
                "investment_note": p.near_term_view,
            }
            for p in picks[:5]
        ]
    }

    result = await call_openai_json(
        system_prompt=(
            "You are a quantitative screening assistant. Given scored stock picks, "
            "write one concise sentence per ticker on near-term (weeks to 3 months) "
            "investment appeal. Be balanced; mention risks. Respond JSON only: "
            '{"picks": [{"ticker": string, "investment_note": string}, ...]}'
        ),
        user_prompt=f"Enrich these top picks:\n{payload}",
        fallback=fallback,
    )

    notes = {item["ticker"]: item.get("investment_note", "") for item in result.get("picks", [])}
    enriched = []
    for pick in picks:
        note = notes.get(pick.ticker)
        if note:
            enriched.append(pick.model_copy(update={"near_term_view": note}))
        else:
            enriched.append(pick)
    return enriched


async def scan_recommendations(
    tickers: Optional[List[str]] = None,
    limit: int = 5,
) -> RecommendationsResponse:
    symbols = []
    for t in (tickers or DEFAULT_SCAN_TICKERS):
        sym = t.upper().strip()
        if sym and sym not in symbols:
            symbols.append(sym)
    symbols = symbols[:12]

    results = await asyncio.gather(*[_scan_one(s) for s in symbols])
    valid = [r for r in results if r is not None]
    valid.sort(key=lambda p: p.score, reverse=True)

    top = valid[:limit]
    top = await _enrich_with_llm(top)

    return RecommendationsResponse(
        disclaimer=DISCLAIMER,
        generated_at=datetime.now(timezone.utc).isoformat(),
        horizon="near-term (approximately 2–12 weeks)",
        picks=top,
        scanned_tickers=symbols,
    )

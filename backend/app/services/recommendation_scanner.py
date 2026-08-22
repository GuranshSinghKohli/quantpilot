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
    "AI-generated screening for educational purposes only, not financial advice. "
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


CATALYST_PATTERNS = (
    ("iphone", "Product-cycle headlines mention a new iPhone — if that launch lands, it can support the tape; not a confirmed move."),
    ("apple event", "An Apple event is in the news — a product reveal can move the name either way."),
    ("robotaxi", "Robotaxi / Cybercab chatter is in the tape — a demo or timeline update is a hint, not a guarantee."),
    ("cybercab", "Robotaxi / Cybercab chatter is in the tape — a demo or timeline update is a hint, not a guarantee."),
    ("fsd", "FSD / autonomy headlines are active — a software or regulatory update could matter near-term."),
    ("unsupervised", "Autonomy / unsupervised-driving headlines are active — treat as a possible catalyst, not a call."),
    ("earnings", "Earnings are in the recent flow — the print and guide can reprice the name quickly."),
    ("guidance", "Guidance talk is in the headlines — a raise or cut can matter more than the print."),
    ("keynote", "A keynote / launch event is being discussed — watch the product, not a promised rally."),
    ("unveil", "A product unveil is in the news — possible near-term driver if it actually ships."),
    ("launch", "Launch / ship headlines are in the mix — a clean event can help; a slip can hurt."),
)


def _headline_titles(headlines: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    titles: List[str] = []
    for item in headlines[:limit]:
        title = (item.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def _catalyst_from_headlines(headlines: List[Dict[str, Any]]) -> Optional[str]:
    blob = " ".join(_headline_titles(headlines)).lower()
    if not blob:
        return None
    for needle, hint in CATALYST_PATTERNS:
        if needle in blob:
            return hint
    return None


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
                "Price in lower third of 52-week range, room for near-term recovery"
            )
        elif position > 0.88:
            score -= 0.1
            reasons.append("Trading near 52-week high, less near-term upside headroom")
        else:
            score += 0.06
            reasons.append("Mid-range vs 52-week high/low, balanced momentum setup")

    if stock.pe_ratio is not None:
        pe = stock.pe_ratio
        if 0 < pe < 18:
            score += 0.1
            reasons.append(f"P/E near {pe:.1f}, relatively modest valuation")
        elif pe > 40:
            score -= 0.08
            reasons.append(f"P/E near {pe:.1f}, premium valuation")
        elif 18 <= pe <= 30:
            score += 0.04
            reasons.append(f"P/E near {pe:.1f}, fair-to-moderate valuation")

    news_delta, news_reason = _news_sentiment(headlines)
    score += news_delta
    reasons.append(news_reason)

    catalyst = _catalyst_from_headlines(headlines)
    if catalyst:
        score += 0.04
        reasons.append(catalyst)

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

    return score, outlook, reasons[:5]


async def _scan_one(
    ticker: str,
) -> Optional[Tuple[RecommendationPick, List[str]]]:
    try:
        stock, headlines = await asyncio.gather(
            get_stock_data(ticker),
            get_ticker_news(ticker),
        )
        score, outlook, reasons = _score_ticker(stock, headlines)
        titles = _headline_titles(headlines, 6)
        catalyst = _catalyst_from_headlines(headlines) or ""

        near_term = {
            "bullish": "Constructive tape — a named catalyst below is a hint, not a call that it will go up.",
            "neutral": "Mixed tape — watch the named event; it can cut either way.",
            "bearish": "Cautious tape — even a product event is only a possible offset, not a rescue.",
        }[outlook]

        return (
            RecommendationPick(
                ticker=stock.ticker,
                score=score,
                outlook=outlook,
                near_term_view=near_term,
                catalyst_hint=catalyst,
                reasons=reasons,
                current_price=stock.current_price,
            ),
            titles,
        )
    except Exception:
        return None


async def _enrich_with_llm(
    picks: List[RecommendationPick],
    headlines_by_ticker: Dict[str, List[str]],
) -> List[RecommendationPick]:
    if not picks:
        return picks

    payload = []
    for pick in picks[:5]:
        row = pick.model_dump()
        row["recent_headlines"] = headlines_by_ticker.get(pick.ticker, [])[:6]
        payload.append(row)

    fallback = {
        "picks": [
            {
                "ticker": p.ticker,
                "investment_note": p.near_term_view,
                "catalyst_hint": p.catalyst_hint,
            }
            for p in picks[:5]
        ]
    }

    result = await call_openai_json(
        system_prompt=(
            "You enrich equity screen picks with catalyst HINTS, not price calls. "
            "Use the recent_headlines. Name a specific upcoming or just-reported "
            "event when the headlines support it (new iPhone cycle, robotaxi/Cybercab "
            "or FSD update, earnings/guide, product keynote). "
            "If headlines are silent, you may cite a well-known near-term product "
            "calendar only as an unverified hint. "
            "Phrase as possibility: 'if X happens, that can support / pressure the tape'. "
            "Never say the stock will go up or is a buy. One counter-risk in the note. "
            "Respond JSON only: "
            '{"picks": [{"ticker": string, "investment_note": string, '
            '"catalyst_hint": string}, ...]}'
        ),
        user_prompt=f"Enrich these top picks with named event hints:\n{payload}",
        fallback=fallback,
    )

    notes = {
        item["ticker"]: item
        for item in result.get("picks", [])
        if item.get("ticker")
    }
    enriched = []
    for pick in picks:
        item = notes.get(pick.ticker) or {}
        note = (item.get("investment_note") or "").strip()
        hint = (item.get("catalyst_hint") or "").strip() or pick.catalyst_hint
        updates: Dict[str, Any] = {}
        if note:
            updates["near_term_view"] = note
        if hint:
            updates["catalyst_hint"] = hint
            if hint not in pick.reasons:
                updates["reasons"] = [hint, *pick.reasons][:5]
        enriched.append(pick.model_copy(update=updates) if updates else pick)
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
    scanned = [r for r in results if r is not None]
    valid = [row[0] for row in scanned]
    headlines_by_ticker = {row[0].ticker: row[1] for row in scanned}
    valid.sort(key=lambda p: p.score, reverse=True)

    top = valid[:limit]
    top = await _enrich_with_llm(top, headlines_by_ticker)

    return RecommendationsResponse(
        disclaimer=DISCLAIMER,
        generated_at=datetime.now(timezone.utc).isoformat(),
        horizon="near-term (approximately 2–12 weeks)",
        picks=top,
        scanned_tickers=symbols,
    )

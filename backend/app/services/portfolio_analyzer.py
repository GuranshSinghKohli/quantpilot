import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.models.agent_schemas import PortfolioAnalysis, PortfolioHolding
from app.observability.logger import get_logger, log_event
from app.routers.watchlist import _load_watchlist
from app.services.yahoo_finance import get_stock_data

logger = get_logger("portfolio_analyzer")

_VALUATION_FROM_PE = [
    (35, "overvalued"),
    (25, "fairly valued"),
    (0, "undervalued"),
]


def _valuation_from_pe(pe: Optional[float]) -> str:
    if pe is None or pe <= 0:
        return "unknown"
    for threshold, label in _VALUATION_FROM_PE:
        if pe >= threshold:
            return label
    return "undervalued"


def _risk_from_pe_and_price(
    pe: Optional[float], price: Optional[float], high: Optional[float]
) -> str:
    if pe is not None and pe > 40:
        return "HIGH"
    if price and high and high > 0 and price / high > 0.95:
        return "MEDIUM"
    if pe is not None and pe < 15:
        return "LOW"
    return "MEDIUM"


async def _analyze_holding(ticker: str) -> PortfolioHolding:
    try:
        stock = await get_stock_data(ticker)
        pe = stock.pe_ratio
        price = stock.current_price
        high = stock.fifty_two_week_high
        return PortfolioHolding(
            ticker=ticker,
            current_price=price,
            pe_ratio=pe,
            risk_level=_risk_from_pe_and_price(pe, price, high),
            valuation=_valuation_from_pe(pe),
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Portfolio holding fetch failed",
            ticker=ticker,
            error=str(exc),
        )
        return PortfolioHolding(ticker=ticker)


async def analyze_portfolio() -> PortfolioAnalysis:
    entries = _load_watchlist()
    tickers = [e.get("ticker", "").upper() for e in entries if e.get("ticker")]
    tickers = list(dict.fromkeys(tickers))

    if not tickers:
        return PortfolioAnalysis(
            summary="Your watchlist is empty. Add tickers to see a portfolio basket view.",
        )

    holdings = await asyncio.gather(*[_analyze_holding(t) for t in tickers])

    pe_values = [h.pe_ratio for h in holdings if h.pe_ratio and h.pe_ratio > 0]
    avg_pe = round(sum(pe_values) / len(pe_values), 2) if pe_values else None

    risk_mix: Dict[str, int] = {}
    for h in holdings:
        risk_mix[h.risk_level] = risk_mix.get(h.risk_level, 0) + 1

    n = len(holdings)
    equal_weight = round(100.0 / n, 1) if n else 0.0
    weighted: List[PortfolioHolding] = []
    for h in holdings:
        weighted.append(
            PortfolioHolding(
                ticker=h.ticker,
                current_price=h.current_price,
                pe_ratio=h.pe_ratio,
                risk_level=h.risk_level,
                valuation=h.valuation,
                weight_pct=equal_weight,
            )
        )

    weakest: Optional[str] = None
    high_risk = [h for h in weighted if h.risk_level == "HIGH"]
    if high_risk:
        weakest = high_risk[0].ticker
    elif pe_values:
        max_pe_h = max(
            (h for h in weighted if h.pe_ratio),
            key=lambda x: x.pe_ratio or 0,
            default=None,
        )
        weakest = max_pe_h.ticker if max_pe_h else None

    sector_note = (
        f"Equal-weight basket of {n} watchlist name{'s' if n != 1 else ''}. "
        "Sector breakdown requires additional data feeds."
    )

    summary_parts = [
        f"{n} holding{'s' if n != 1 else ''}",
        f"avg P/E {avg_pe}" if avg_pe else "partial P/E data",
    ]
    if weakest:
        summary_parts.append(f"watch {weakest} closely")

    return PortfolioAnalysis(
        holdings=weighted,
        avg_pe=avg_pe,
        risk_mix=risk_mix,
        sector_note=sector_note,
        weakest_ticker=weakest,
        summary=" · ".join(p for p in summary_parts if p),
    )

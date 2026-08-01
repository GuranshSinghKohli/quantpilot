import asyncio
import logging
from typing import Dict, List, Optional

from app.models.agent_schemas import PortfolioAnalysis, PortfolioHolding
from app.observability.logger import get_logger, log_event
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


async def _analyze_holding(
    ticker: str, position: Optional[Dict[str, float]] = None
) -> PortfolioHolding:
    position = position or {}
    try:
        stock = await get_stock_data(ticker)
        pe = stock.pe_ratio
        price = stock.current_price
        high = stock.fifty_two_week_high
        shares = position.get("shares")
        avg_cost = position.get("avg_cost")
        market_value = shares * price if shares and price else None
        gain_pct = (
            round(((price - avg_cost) / avg_cost) * 100, 2)
            if price and avg_cost and avg_cost > 0
            else None
        )
        return PortfolioHolding(
            ticker=ticker,
            current_price=price,
            pe_ratio=pe,
            risk_level=_risk_from_pe_and_price(pe, price, high),
            valuation=_valuation_from_pe(pe),
            shares=shares,
            avg_cost=avg_cost,
            market_value=round(market_value, 2) if market_value else None,
            unrealized_gain_pct=gain_pct,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Portfolio holding fetch failed",
            ticker=ticker,
            error=str(exc),
        )
        return PortfolioHolding(
            ticker=ticker,
            shares=position.get("shares"),
            avg_cost=position.get("avg_cost"),
        )


async def analyze_portfolio(
    tickers: Optional[List[str]] = None,
    positions: Optional[Dict[str, Dict[str, float]]] = None,
) -> PortfolioAnalysis:
    raw = tickers or []
    tickers = list(dict.fromkeys(t.upper().strip() for t in raw if t and t.strip()))
    positions = positions or {}

    if not tickers:
        return PortfolioAnalysis(
            summary="Your watchlist is empty. Add tickers to see a portfolio basket view.",
        )

    holdings = await asyncio.gather(
        *[_analyze_holding(t, positions.get(t)) for t in tickers]
    )

    pe_values = [h.pe_ratio for h in holdings if h.pe_ratio and h.pe_ratio > 0]
    avg_pe = round(sum(pe_values) / len(pe_values), 2) if pe_values else None

    risk_mix: Dict[str, int] = {}
    for h in holdings:
        risk_mix[h.risk_level] = risk_mix.get(h.risk_level, 0) + 1

    n = len(holdings)
    total_market_value = sum(h.market_value for h in holdings if h.market_value)
    weighted_by_real_positions = total_market_value > 0 and all(
        h.market_value for h in holdings
    )
    equal_weight = round(100.0 / n, 1) if n else 0.0
    weighted: List[PortfolioHolding] = []
    for h in holdings:
        if weighted_by_real_positions and h.market_value:
            weight = round((h.market_value / total_market_value) * 100, 1)
        else:
            weight = equal_weight
        weighted.append(
            PortfolioHolding(
                ticker=h.ticker,
                current_price=h.current_price,
                pe_ratio=h.pe_ratio,
                risk_level=h.risk_level,
                valuation=h.valuation,
                weight_pct=weight,
                shares=h.shares,
                avg_cost=h.avg_cost,
                market_value=h.market_value,
                unrealized_gain_pct=h.unrealized_gain_pct,
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
        ("Weighted by real position sizes (shares × price). " if weighted_by_real_positions else "Equal-weight basket. ")
        + f"{n} name{'s' if n != 1 else ''}. Sector breakdown requires additional data feeds."
    )

    summary_parts = [
        f"{n} holding{'s' if n != 1 else ''}",
        f"avg P/E {avg_pe}" if avg_pe else "partial P/E data",
    ]
    if weighted_by_real_positions:
        summary_parts.append(f"${total_market_value:,.0f} total value")
    if weakest:
        summary_parts.append(f"watch {weakest} closely")

    return PortfolioAnalysis(
        holdings=weighted,
        avg_pe=avg_pe,
        risk_mix=risk_mix,
        sector_note=sector_note,
        weakest_ticker=weakest,
        summary=" · ".join(p for p in summary_parts if p),
        total_market_value=round(total_market_value, 2) if total_market_value else None,
        weighted_by_real_positions=weighted_by_real_positions,
    )

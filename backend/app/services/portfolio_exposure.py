"""Phase 2 — portfolio weight for a ticker from holdings (no trade advice)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def holding_notional(
    holding: Any,
    *,
    mark_price: Optional[float] = None,
) -> float:
    """Dollar notional: shares × mark (current price, else avg cost)."""
    shares = _as_float(getattr(holding, "shares", None))
    if shares is None and isinstance(holding, dict):
        shares = _as_float(holding.get("shares"))
    if shares is None or shares <= 0:
        return 0.0

    price = mark_price
    if price is None or price <= 0:
        avg = getattr(holding, "avg_cost", None)
        if avg is None and isinstance(holding, dict):
            avg = holding.get("avg_cost")
        price = _as_float(avg)
    if price is None or price <= 0:
        return 0.0
    return shares * price


def portfolio_weight(
    ticker: str,
    holdings: Iterable[Any],
    *,
    mark_prices: Optional[Dict[str, float]] = None,
) -> float:
    """
    Fraction of sized book in `ticker`.

    Watchlist-only rows (no shares) contribute 0. If the book has no sized
    positions, weight is 0 — the gate then treats exposure as unknown/neutral
    rather than inventing an equal-weight book.
    """
    symbol = (ticker or "").upper().strip()
    marks = {k.upper().strip(): v for k, v in (mark_prices or {}).items()}
    notionals: Dict[str, float] = {}
    for holding in holdings or []:
        hticker = getattr(holding, "ticker", None)
        if hticker is None and isinstance(holding, dict):
            hticker = holding.get("ticker")
        if not hticker:
            continue
        key = str(hticker).upper().strip()
        mark = marks.get(key)
        notionals[key] = notionals.get(key, 0.0) + holding_notional(
            holding, mark_price=mark
        )

    total = sum(notionals.values())
    if total <= 0:
        return 0.0
    return max(0.0, min(1.0, notionals.get(symbol, 0.0) / total))


def event_impact(magnitude_pct: Optional[float], exposure: float) -> float:
    """Approximate book impact in percent-of-portfolio points."""
    if magnitude_pct is None:
        return 0.0
    return abs(float(magnitude_pct)) * max(0.0, float(exposure))

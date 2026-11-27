"""Value objects passed across the repository boundary.

The fill engine and the risk engine stay pure. They build these objects.
They do not receive a session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.league.models import Portfolio, Position


@dataclass(frozen=True)
class BarInput:
    ticker: str
    t: datetime
    o: Decimal
    h: Decimal
    l: Decimal
    c: Decimal
    v: int
    feed: str


@dataclass(frozen=True)
class FillInput:
    price: Decimal
    qty: Decimal
    slippage_bps: Decimal
    bar_time: datetime
    filled_at: datetime


@dataclass(frozen=True)
class PortfolioView:
    portfolio: Portfolio
    positions: tuple[Position, ...]

"""Trading-session calendar derived from canonical market_bars.

Windows are session offsets, not calendar days. Missing bars are never
forward-filled. v1 uses unadjusted close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.ingestion_models import MarketBar
from app.market import DAILY_INTERVAL

WINDOW_OFFSETS = {"m1": -1, "p1": 1, "p3": 3, "p5": 5, "p20": 20}


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def event_timestamp(*, occurred_at: Optional[datetime], published_at: Optional[datetime]) -> Optional[datetime]:
    """Occurrence first. Publication is not pretended to be event time."""
    if occurred_at is not None:
        return _as_utc(occurred_at)
    if published_at is not None:
        return _as_utc(published_at)
    return None


def event_calendar_date(ts: datetime) -> date:
    return _as_utc(ts).date()


@dataclass(frozen=True)
class SessionBar:
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def bar_is_valid(bar: SessionBar) -> bool:
    if bar.low <= 0 or bar.high <= 0 or bar.open <= 0 or bar.close <= 0:
        return False
    if bar.low > bar.high:
        return False
    if bar.volume < 0:
        return False
    return True


def load_sessions(db: Session, symbol: str, *, interval: str = DAILY_INTERVAL) -> List[SessionBar]:
    """One session per trading_date. Newest ingested bar wins when vendors collide."""
    rows = list(
        db.scalars(
            select(MarketBar)
            .where(MarketBar.symbol == symbol.upper(), MarketBar.interval == interval)
            .order_by(MarketBar.trading_date.asc(), MarketBar.source_ingested_at.desc(), MarketBar.event_id.desc())
        )
    )
    seen: Dict[date, SessionBar] = {}
    for row in rows:
        if row.trading_date in seen:
            continue
        seen[row.trading_date] = SessionBar(
            trading_date=row.trading_date,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=int(row.volume),
        )
    return [seen[day] for day in sorted(seen)]


class TradingCalendar:
    """Session lookup for one symbol from actual bars."""

    def __init__(self, sessions: Sequence[SessionBar]):
        self.sessions = [bar for bar in sessions if bar_is_valid(bar)]
        self._index = {bar.trading_date: i for i, bar in enumerate(self.sessions)}

    def dates(self) -> List[date]:
        return [bar.trading_date for bar in self.sessions]

    def get(self, day: date) -> Optional[SessionBar]:
        idx = self._index.get(day)
        return None if idx is None else self.sessions[idx]

    def event_trading_date(self, ts: datetime) -> Optional[date]:
        """UTC calendar date if a session exists; otherwise the next later session."""
        calendar = event_calendar_date(ts)
        if calendar in self._index:
            return calendar
        for bar in self.sessions:
            if bar.trading_date > calendar:
                return bar.trading_date
        return None

    def offset(self, t0: date, n: int) -> Optional[SessionBar]:
        idx = self._index.get(t0)
        if idx is None:
            return None
        target = idx + n
        if target < 0 or target >= len(self.sessions):
            return None
        return self.sessions[target]

    def prior(self, t0: date, count: int) -> List[SessionBar]:
        idx = self._index.get(t0)
        if idx is None or count <= 0:
            return []
        start = idx - count
        if start < 0:
            return []
        return list(self.sessions[start:idx])

    def window(self, t0: date, start: int, end: int) -> Optional[List[SessionBar]]:
        """Inclusive session slice from T+start through T+end. None if any session is missing."""
        if start > end:
            return None
        bars = []
        for n in range(start, end + 1):
            bar = self.offset(t0, n)
            if bar is None:
                return None
            bars.append(bar)
        return bars


def simple_return(start: Optional[Decimal], end: Optional[Decimal]) -> Optional[float]:
    """(P_end / P_start) - 1. Unavailable when either price is missing or non-positive."""
    if start is None or end is None:
        return None
    if start <= 0 or end <= 0:
        return None
    return float(end / start) - 1.0


def sample_stdev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((item - mean) ** 2 for item in values) / (len(values) - 1)
    return var ** 0.5


def daily_returns(closes: Sequence[Decimal]) -> Optional[List[float]]:
    if len(closes) < 2:
        return None
    out: List[float] = []
    for prev, cur in zip(closes, closes[1:]):
        ret = simple_return(prev, cur)
        if ret is None:
            return None
        out.append(ret)
    return out

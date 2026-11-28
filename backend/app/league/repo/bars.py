"""league.bars. The poller writes. The fill engine reads through first_bar_after."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.league.models import Bar
from app.league.repo.types import BarInput


def upsert_bars(session: Session, bars: Sequence[BarInput]) -> int:
    """Insert bars. A repeat of (ticker, t) leaves the first row unchanged."""
    if not bars:
        return 0
    statement = pg_insert(Bar).values(
        [
            {
                "ticker": bar.ticker,
                "t": bar.t,
                "o": bar.o,
                "h": bar.h,
                "l": bar.l,
                "c": bar.c,
                "v": bar.v,
                "feed": bar.feed,
            }
            for bar in bars
        ]
    )
    statement = statement.on_conflict_do_nothing(index_elements=["ticker", "t"])
    result = session.execute(statement)
    return int(result.rowcount)


def first_bar_after(
    session: Session,
    ticker: str,
    after_t: datetime,
    until_t: datetime,
    *,
    inclusive: bool = False,
) -> Bar | None:
    """First bar in (after_t, until_t], or [after_t, until_t] when inclusive.

    A missing minute is skipped. The default stays strict so a bar at exactly
    ``after_t`` is not a fill for an order accepted at that time.
    """
    lower = Bar.t >= after_t if inclusive else Bar.t > after_t
    return session.scalar(
        select(Bar)
        .where(Bar.ticker == ticker, lower, Bar.t <= until_t)
        .order_by(Bar.t.asc())
        .limit(1)
    )

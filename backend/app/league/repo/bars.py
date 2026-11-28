"""league.bars. The poller writes. Fills and marks read through different cuts.

A fill uses the bar open via first_bar_after. A mark uses latest_known_prices:
the close of a bar timestamped t is known only once that minute has finished.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
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


def latest_known_prices(
    session: Session,
    tickers: Sequence[str],
    now: datetime,
) -> dict[str, Decimal]:
    """Close of the newest bar with t + 1 minute <= now.

    The bar timestamp is the start of the minute. At 10:00 the 10:00 close is
    not known. At 10:01 it is. A ticker with no completed minute is omitted.
    An empty ticker list returns an empty dict and does not query.
    """
    names = list(dict.fromkeys(tickers))
    if not names:
        return {}
    cutoff = now - timedelta(minutes=1)
    rows = session.execute(
        select(Bar.ticker, Bar.t, Bar.c)
        .where(Bar.ticker.in_(names), Bar.t <= cutoff)
        .order_by(Bar.ticker.asc(), Bar.t.desc())
    ).all()
    found: dict[str, Decimal] = {}
    for ticker, _when, close in rows:
        if ticker not in found:
            found[ticker] = close
    return found

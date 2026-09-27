"""SYNTHETIC market events for exercising the pipeline before real adapters exist.

Values are generated from the index alone, so the same ``count`` always yields
the same records and therefore the same ``event_id``s (``ingested_at`` is not
part of the content hash). The source ``synthetic.market`` is flagged
``synthetic=True`` in the registry and is never market data.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from app.pipeline.contracts import MarketRecord, build_event

SOURCE = "synthetic.market"
SYMBOL = "SYNTH"
_FIRST_DAY = date(2026, 1, 2)


def _trading_days(count: int) -> List[date]:
    days, day = [], _FIRST_DAY
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market_records(count: int, *, ingested_at: Optional[datetime] = None) -> List[MarketRecord]:
    ingested_at = ingested_at or datetime.now(timezone.utc)
    records = []
    for index, day in enumerate(_trading_days(count)):
        base = Decimal(100) + Decimal(index) / 4
        records.append(
            MarketRecord.create(
                source=SOURCE,
                symbol=SYMBOL,
                timestamp=datetime.combine(day, time(21, 0), timezone.utc),
                trading_date=day,
                open=base,
                high=base + 2,
                low=base - 1,
                close=base + 1,
                adj_close=base + 1,
                volume=10_000 + index,
                currency="USD",
                ingested_at=ingested_at,
            )
        )
    return records


def market_events(count: int, *, run_id: uuid.UUID, ingested_at: Optional[datetime] = None):
    return [
        build_event(record, run_id=run_id, original={"synthetic": True, "index": index})
        for index, record in enumerate(market_records(count, ingested_at=ingested_at))
    ]

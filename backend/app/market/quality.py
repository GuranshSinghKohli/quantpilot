"""Read-only market/event quality checks. Never writes canonical market data."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import EventStatus, ResolutionStatus
from app.domain.ingestion_models import MarketBar
from app.domain.models import Event, EventCandidate, EventEntity, EventImpact
from app.graph.resolve import EntityRef, resolve_entity
from app.market import CALCULATION_VERSION
from app.observability.logger import get_logger, log_event

logger = get_logger("market")


def _ohlc_ok(bar: MarketBar) -> bool:
    return bar.low > 0 and bar.high > 0 and bar.open > 0 and bar.close > 0 and bar.low <= bar.high


def market_quality(db: Session) -> dict:
    started = time.perf_counter()
    events = list(db.scalars(select(Event)))
    candidates = list(db.scalars(select(EventCandidate)))
    links = list(db.scalars(select(EventEntity)))
    impacts = list(db.scalars(select(EventImpact)))
    bars = list(db.scalars(select(MarketBar)))

    event_ids = {event.id for event in events}
    links_by_event: Dict[Any, List[EventEntity]] = defaultdict(list)
    for link in links:
        links_by_event[link.event_id].append(link)

    event_issues = {
        "missing_event_type": [str(e.id) for e in events if not e.event_type],
        "missing_timestamp": [
            str(e.id) for e in events if e.occurred_at is None and e.published_at is None
        ],
        "missing_source": [str(e.id) for e in events if e.primary_source_id is None],
        "missing_entity_association": [
            str(e.id)
            for e in events
            if e.status == EventStatus.CONFIRMED.value and e.id not in links_by_event
        ],
        "duplicate_identity": [],
    }
    dedup_counts: Dict[str, int] = defaultdict(int)
    for event in events:
        dedup_counts[event.dedup_key] += 1
    event_issues["duplicate_identity"] = [key for key, count in dedup_counts.items() if count > 1]

    unresolved_entities: List[str] = []
    ambiguous_entities: List[str] = []
    for event in events:
        if event.status != EventStatus.CONFIRMED.value:
            continue
        for link in links_by_event.get(event.id, []):
            result = resolve_entity(db, EntityRef(entity_id=link.entity_id))
            if result.status == ResolutionStatus.UNRESOLVED:
                unresolved_entities.append(str(link.entity_id))
            if result.status == ResolutionStatus.AMBIGUOUS:
                ambiguous_entities.append(str(link.entity_id))
    event_issues["unresolved_entity"] = sorted(set(unresolved_entities))
    event_issues["ambiguous_entity"] = sorted(set(ambiguous_entities))

    market_issues = {
        "invalid_ohlc": [],
        "negative_volume": [],
        "missing_trading_date": [],
        "duplicate_symbol_date": [],
        "impossible_price_relationship": [],
    }
    bar_keys: Dict[tuple, int] = defaultdict(int)
    for bar in bars:
        key = (bar.symbol, bar.interval, bar.trading_date)
        bar_keys[key] += 1
        if bar.trading_date is None:
            market_issues["missing_trading_date"].append(str(bar.id))
        if bar.volume < 0:
            market_issues["negative_volume"].append(str(bar.id))
        if not _ohlc_ok(bar):
            market_issues["invalid_ohlc"].append(str(bar.id))
            if bar.low > bar.high:
                market_issues["impossible_price_relationship"].append(str(bar.id))
    market_issues["duplicate_symbol_date"] = [
        f"{symbol}:{interval}:{day.isoformat()}"
        for (symbol, interval, day), count in bar_keys.items()
        if count > 1
    ]

    impact_issues = {
        "missing_baseline": [str(row.id) for row in impacts if row.baseline_price is None],
        "missing_window_data": [
            str(row.id)
            for row in impacts
            if row.return_p1 is None or row.return_p3 is None or row.return_p5 is None or row.return_p20 is None
        ],
        "missing_benchmark": [
            str(row.id)
            for row in impacts
            if row.benchmark_return_p1 is None
            and row.benchmark_return_p3 is None
            and row.benchmark_return_p5 is None
            and row.benchmark_return_p20 is None
        ],
        "insufficient_volatility_history": [
            str(row.id) for row in impacts if row.pre_event_volatility is None
        ],
        "invalid_return": [
            str(row.id)
            for row in impacts
            if any(
                value is not None and value <= -1
                for value in (row.return_m1, row.return_p1, row.return_p3, row.return_p5, row.return_p20)
            )
        ],
        "calculation_version_mismatch": [
            str(row.id) for row in impacts if row.calculation_version != CALCULATION_VERSION
        ],
    }

    report = {
        "events": {
            "total": len(events),
            "confirmed": sum(1 for e in events if e.status == EventStatus.CONFIRMED.value),
            **{key: {"count": len(value), "ids": value[:50]} for key, value in event_issues.items()},
        },
        "candidates": {
            "total": len(candidates),
            "pending": sum(1 for c in candidates if c.status == "PENDING"),
            "accepted": sum(1 for c in candidates if c.status == "ACCEPTED"),
            "rejected": sum(1 for c in candidates if c.status == "REJECTED"),
            "ambiguous": sum(1 for c in candidates if c.status == "AMBIGUOUS"),
        },
        "market_bars": {
            "total": len(bars),
            **{key: {"count": len(value), "ids": value[:50]} for key, value in market_issues.items()},
        },
        "impacts": {
            "total": len(impacts),
            **{key: {"count": len(value), "ids": value[:50]} for key, value in impact_issues.items()},
        },
        "read_only": True,
    }
    duration = time.perf_counter() - started
    log_event(
        logger,
        20,
        "MARKET_QUALITY",
        component="market",
        operation="quality",
        status="ok",
        duration_ms=int(duration * 1000),
    )
    return report

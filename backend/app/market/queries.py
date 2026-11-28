"""Bounded read helpers for event intelligence APIs."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.models import Event, EventEntity, EventImpact
from app.graph.queries import MAX_RESULTS, entity_payload, get_entity

IMPACT_LIMITATIONS = [
    "market data is daily",
    "no intraday timing",
    "v1 uses unadjusted close",
    "benchmark-adjusted return is not causal attribution",
    "missing bars are unavailable and never forward-filled",
]


def get_event(db: Session, event_id: uuid.UUID) -> Optional[Event]:
    return db.get(Event, event_id)


def event_payload(event: Event) -> Dict[str, Any]:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "title": event.title,
        "summary": event.summary,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "published_at": event.published_at.isoformat() if event.published_at else None,
        "detected_at": event.detected_at.isoformat(),
        "source_id": str(event.primary_source_id) if event.primary_source_id else None,
        "evidence_id": str(event.evidence_id) if event.evidence_id else None,
        "status": event.status,
    }


def event_entities(db: Session, event_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    rows = list(
        db.scalars(
            select(EventEntity)
            .options(selectinload(EventEntity.entity))
            .where(EventEntity.event_id == event_id)
            .limit(min(limit, MAX_RESULTS))
        )
    )
    out = []
    for row in rows:
        entity = get_entity(db, row.entity_id)
        out.append(
            {
                "entity_id": str(row.entity_id),
                "role": row.role,
                "source_id": str(row.source_id) if row.source_id else None,
                "evidence_id": str(row.evidence_id) if row.evidence_id else None,
                "confidence": row.confidence,
                "entity": entity_payload(entity) if entity is not None else None,
            }
        )
    return out


def _window_block(impact: EventImpact, suffix: str) -> Dict[str, Optional[float]]:
    return {
        "return": getattr(impact, f"return_{suffix}"),
        "benchmark_return": getattr(impact, f"benchmark_return_{suffix}", None)
        if suffix != "m1"
        else None,
        "relative_return": getattr(impact, f"relative_return_{suffix}", None)
        if suffix != "m1"
        else None,
        "available": getattr(impact, f"return_{suffix}") is not None,
    }


def impact_payload(impact: EventImpact) -> Dict[str, Any]:
    return {
        "impact_id": str(impact.id),
        "event_id": str(impact.event_id),
        "entity_id": str(impact.entity_id),
        "asset": {"symbol": impact.symbol},
        "event_trading_date": impact.event_trading_date.isoformat() if impact.event_trading_date else None,
        "observed": {
            "baseline_price": impact.baseline_price,
            "first_market_date": impact.first_market_date.isoformat() if impact.first_market_date else None,
            "last_market_date": impact.last_market_date.isoformat() if impact.last_market_date else None,
        },
        "windows": {
            "-1D": _window_block(impact, "m1"),
            "+1D": _window_block(impact, "p1"),
            "+3D": _window_block(impact, "p3"),
            "+5D": _window_block(impact, "p5"),
            "+20D": _window_block(impact, "p20"),
        },
        "volume": {
            "event_day_volume": impact.event_day_volume,
            "average_pre_event_volume": impact.pre_event_average_volume,
            "volume_ratio": impact.volume_ratio,
        },
        "volatility": {
            "pre_event_volatility": impact.pre_event_volatility,
            "event_window_volatility": impact.event_window_volatility,
        },
        "calculation_version": impact.calculation_version,
        "calculation_job_id": str(impact.calculation_job_id) if impact.calculation_job_id else None,
        "calculated_at": impact.calculated_at.isoformat(),
        "limitations": list(IMPACT_LIMITATIONS),
    }


def event_impacts(db: Session, event_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    rows = list(
        db.scalars(
            select(EventImpact)
            .where(EventImpact.event_id == event_id)
            .limit(min(limit, MAX_RESULTS))
        )
    )
    return [impact_payload(row) for row in rows]


def entity_events(db: Session, entity_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    rows = list(
        db.scalars(
            select(EventEntity)
            .options(selectinload(EventEntity.event))
            .where(EventEntity.entity_id == entity_id)
            .limit(min(limit, MAX_RESULTS))
        )
    )
    out = []
    for row in rows:
        if row.event is None:
            continue
        payload = event_payload(row.event)
        payload["role"] = row.role
        out.append(payload)
    return out


def entity_market_impacts(db: Session, entity_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    rows = list(
        db.scalars(
            select(EventImpact)
            .where(EventImpact.entity_id == entity_id)
            .limit(min(limit, MAX_RESULTS))
        )
    )
    return [impact_payload(row) for row in rows]

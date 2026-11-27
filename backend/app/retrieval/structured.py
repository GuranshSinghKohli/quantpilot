"""Deterministic structured context from Postgres. Neo4j is not queried."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import EventStatus
from app.retrieval.time import window as utc_window
from app.domain.models import (
    Company,
    Entity,
    Event,
    EventEntity,
    EventImpact,
    EvidenceRecord,
    Relationship,
    Source,
)
from app.core.settings import get_settings
from app.graph.queries import entity_payload
from app.market.binding import WINDOW_DEFINITION, matching_security
from app.market.queries import impact_payload


def load_entities(db: Session, entity_ids: Sequence[uuid.UUID], *, limit: int) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    rows = list(
        db.scalars(
            select(Entity)
            .options(selectinload(Entity.company), selectinload(Entity.technology))
            .where(Entity.id.in_(list(entity_ids)))
            .limit(limit)
        )
    )
    rows.sort(key=lambda item: str(item.id))
    out = []
    for entity in rows:
        payload = entity_payload(entity)
        company = entity.company
        payload["source"] = {
            "record": "companies" if company is not None else "entities",
            "source": company.source if company is not None else entity.source_system,
            "raw_uri": company.raw_uri if company is not None else None,
            "content_hash": company.content_hash if company is not None else None,
        }
        out.append(payload)
    return out


def load_events(
    db: Session,
    *,
    entity_ids: Sequence[uuid.UUID],
    event_ids: Sequence[uuid.UUID],
    date_from: Optional[datetime],
    date_to: Optional[datetime],
    limit: int,
) -> List[Dict[str, Any]]:
    stmt = select(Event).where(Event.status == EventStatus.CONFIRMED.value)
    if event_ids:
        stmt = stmt.where(Event.id.in_(list(event_ids)))
    elif entity_ids:
        linked = select(EventEntity.event_id).where(EventEntity.entity_id.in_(list(entity_ids)))
        stmt = stmt.where(Event.id.in_(linked))
    start, end = utc_window(date_from, date_to)
    if start is not None or end is not None:
        stmt = stmt.where(Event.occurred_at.is_not(None))
    if start is not None:
        stmt = stmt.where(Event.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(Event.occurred_at < end)
    events = list(db.scalars(stmt.order_by(Event.occurred_at.desc().nullslast(), Event.id).limit(limit)))
    out = []
    for event in events:
        source = db.get(Source, event.primary_source_id) if event.primary_source_id else None
        out.append(
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "title": event.title,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "published_at": event.published_at.isoformat() if event.published_at else None,
                "source_id": str(event.primary_source_id) if event.primary_source_id else None,
                "evidence_id": str(event.evidence_id) if event.evidence_id else None,
                "source_url": source.url if source is not None else None,
                "publisher": source.publisher if source is not None else None,
            }
        )
    return out


def load_graph_facts(db: Session, entity_ids: Sequence[uuid.UUID], *, limit: int) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    stmt = (
        select(Relationship)
        .options(selectinload(Relationship.evidence_links))
        .where(
            or_(
                Relationship.subject_entity_id.in_(list(entity_ids)),
                Relationship.object_entity_id.in_(list(entity_ids)),
            )
        )
        .order_by(Relationship.id)
        .limit(limit * 2)
    )
    out = []
    for edge in db.scalars(stmt):
        links = list(edge.evidence_links)
        if not links:
            continue
        link = min(links, key=lambda item: str(item.evidence_id))
        record = db.get(EvidenceRecord, link.evidence_id)
        source = db.get(Source, record.source_id) if record is not None else None
        source_id = str(record.source_id) if record is not None else None
        out.append(
            {
                "relationship_id": str(edge.id),
                "from_entity": str(edge.subject_entity_id),
                "relationship": edge.relationship_type,
                "to_entity": str(edge.object_entity_id),
                "status": edge.status,
                "source_id": source_id,
                "evidence_id": str(link.evidence_id),
                "source_url": source.url if source is not None else None,
                "publisher": source.publisher if source is not None else None,
            }
        )
        if len(out) >= limit:
            break
    return out


def load_market_facts(
    db: Session,
    event_ids: Sequence[uuid.UUID],
    *,
    securities: Sequence[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Impacts for the resolved securities that are explicitly linked to the event.

    An event id alone is not enough. The impact entity and symbol must be one
    resolved security, and an event_entities row must name that entity.
    """
    if not event_ids or not securities:
        return []
    entity_ids = []
    symbols = []
    for row in securities:
        try:
            entity_ids.append(uuid.UUID(str(row.get("entity_id"))))
        except (TypeError, ValueError):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            symbols.append(symbol)
    if not entity_ids or not symbols:
        return []
    linked = {
        (event_id, entity_id)
        for event_id, entity_id in db.execute(
            select(EventEntity.event_id, EventEntity.entity_id).where(
                EventEntity.event_id.in_(list(event_ids)),
                EventEntity.entity_id.in_(entity_ids),
            )
        )
    }
    rows = list(
        db.scalars(
            select(EventImpact)
            .join(
                EventEntity,
                (EventEntity.event_id == EventImpact.event_id)
                & (EventEntity.entity_id == EventImpact.entity_id),
            )
            .where(
                EventImpact.event_id.in_(list(event_ids)),
                EventImpact.entity_id.in_(entity_ids),
                EventImpact.symbol.in_(symbols),
            )
            .distinct()
            .order_by(EventImpact.event_id, EventImpact.symbol)
            .limit(limit)
        )
    )
    benchmark = get_settings().MARKET_BENCHMARK_SYMBOL
    out = []
    for impact in rows:
        if (impact.event_id, impact.entity_id) not in linked:
            continue
        payload = impact_payload(impact)
        security = matching_security(payload, securities)
        if security is None:
            continue
        payload["symbol"] = impact.symbol
        payload["canonical_name"] = security.get("canonical_name")
        payload["exchange"] = security.get("exchange")
        payload["security_type"] = security.get("security_type") or "equity"
        payload["benchmark_symbol"] = benchmark
        payload["window_definition"] = WINDOW_DEFINITION
        payload["identity_verified"] = True
        payload["source"] = {
            "record": "event_impacts",
            "event_id": str(impact.event_id),
            "calculation_version": impact.calculation_version,
        }
        out.append(payload)
    return out


def company_record(db: Session, entity_id: uuid.UUID) -> Optional[Company]:
    return db.get(Company, entity_id)

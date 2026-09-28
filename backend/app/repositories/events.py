"""Detected events and the entities they involve."""

from __future__ import annotations

import uuid
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import EventStatus
from app.domain.identity import event_dedup_key
from app.domain.models import Event, EventEntity, Source
from app.domain.schemas import EventIn
from app.repositories._upsert import insert_ignore


def upsert_event(db: Session, data: EventIn) -> Tuple[Event, bool]:
    """Keyed on dedup_key (type + subject entity + UTC day + primary source).
    Entity links are merged into an existing event rather than duplicated."""
    source_hash: Optional[str] = None
    if data.primary_source_id is not None:
        source = db.get(Source, data.primary_source_id)
        if source is None:
            raise LookupError(f"Source {data.primary_source_id} not found")
        source_hash = source.url_hash
    key = event_dedup_key(
        data.event_type.value, data.primary_entity_id, data.occurred_at, source_hash
    )
    created = insert_ignore(
        db,
        Event,
        {
            "id": uuid.uuid4(),
            "event_type": data.event_type.value,
            "title": data.title,
            "summary": data.summary,
            "occurred_at": data.occurred_at,
            "published_at": data.published_at,
            "detected_at": data.detected_at,
            "primary_source_id": data.primary_source_id,
            "evidence_id": data.evidence_id,
            "dedup_key": key,
            "status": EventStatus.DETECTED.value,
        },
        ["dedup_key"],
    )
    event = db.scalars(select(Event).where(Event.dedup_key == key)).one()
    for link in data.entities:
        insert_ignore(
            db,
            EventEntity,
            {
                "event_id": event.id,
                "entity_id": link.entity_id,
                "role": link.role.value,
                "source_id": link.source_id,
                "evidence_id": link.evidence_id,
                "confidence": link.confidence,
            },
            ["event_id", "entity_id", "role"],
        )
    db.flush()
    return event, created

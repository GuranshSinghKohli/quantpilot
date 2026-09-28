"""Read-only event intelligence API. No Cypher, no writes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.graph.queries import MAX_RESULTS
from app.market.queries import (
    entity_events,
    entity_market_impacts,
    event_entities,
    event_impacts,
    event_payload,
    get_event,
)

router = APIRouter(tags=["events"])


def _event_or_404(db: Session, event_id: uuid.UUID):
    event = get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("/events/{event_id}")
def read_event(event_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    return event_payload(_event_or_404(db, event_id))


@router.get("/events/{event_id}/entities")
def read_event_entities(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=MAX_RESULTS),
) -> dict:
    _event_or_404(db, event_id)
    return {"event_id": str(event_id), "entities": event_entities(db, event_id, limit=limit)}


@router.get("/events/{event_id}/impact")
def read_event_impact(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=MAX_RESULTS),
) -> dict:
    event = _event_or_404(db, event_id)
    return {
        "event": event_payload(event),
        "impacts": event_impacts(db, event_id, limit=limit),
    }

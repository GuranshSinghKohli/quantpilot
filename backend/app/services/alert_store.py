"""In-app notification (AlertEvent) persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, User
from app.models.alert_schemas import AlertEventOut


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def event_to_out(event: AlertEvent) -> AlertEventOut:
    return AlertEventOut(
        id=event.id,
        ticker=event.ticker,
        alert_type=event.alert_type,
        title=event.title,
        message=event.message,
        observed_value=event.observed_value,
        threshold=event.threshold,
        investigation_id=getattr(event, "investigation_id", None),
        materiality_score=getattr(event, "materiality_score", None),
        created_at=_iso(event.created_at) or datetime.now(timezone.utc).isoformat(),
        read_at=_iso(event.read_at),
        is_read=event.read_at is not None,
        feedback=getattr(event, "feedback", "") or "",
    )


def list_events(
    db: Session,
    user: User,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> List[AlertEvent]:
    stmt = select(AlertEvent).where(AlertEvent.user_id == user.id)
    if unread_only:
        stmt = stmt.where(AlertEvent.read_at.is_(None))
    stmt = stmt.order_by(AlertEvent.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def unread_count(db: Session, user: User) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AlertEvent)
            .where(AlertEvent.user_id == user.id, AlertEvent.read_at.is_(None))
        )
        or 0
    )


def mark_event_read(db: Session, user: User, event_id: int) -> Optional[AlertEvent]:
    event = db.scalar(
        select(AlertEvent).where(AlertEvent.id == event_id, AlertEvent.user_id == user.id)
    )
    if event is None:
        return None
    if event.read_at is None:
        event.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(event)
    return event


def mark_all_read(db: Session, user: User) -> int:
    events = list_events(db, user, unread_only=True, limit=500)
    now = datetime.now(timezone.utc)
    for event in events:
        event.read_at = now
    db.commit()
    return len(events)

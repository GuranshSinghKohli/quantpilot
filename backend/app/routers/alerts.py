"""Research-event notifications. No price-alert rules or trading signals."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.models.alert_schemas import AlertEventOut, AlertFeedbackRequest
from app.services import alert_store, intelligence_dashboard

router = APIRouter(tags=["notifications"])


@router.get("/alerts/events", response_model=List[AlertEventOut])
def list_alert_events(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[AlertEventOut]:
    rows = alert_store.list_events(
        db, user, unread_only=unread_only, limit=limit
    )
    return [alert_store.event_to_out(e) for e in rows]


@router.post("/alerts/events/{event_id}/read", response_model=AlertEventOut)
def mark_alert_event_read(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AlertEventOut:
    event = alert_store.mark_event_read(db, user, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return alert_store.event_to_out(event)


@router.post("/alerts/events/{event_id}/feedback", response_model=AlertEventOut)
def set_alert_event_feedback(
    event_id: int,
    body: AlertFeedbackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AlertEventOut:
    event = intelligence_dashboard.set_alert_feedback(
        db, user, event_id, body.feedback
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return alert_store.event_to_out(event)

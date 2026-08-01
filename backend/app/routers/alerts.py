"""Alert API routes (Phase 9)."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.jobs.alert_job import run_alert_evaluation
from app.models.alert_schemas import (
    AlertEvaluateResponse,
    AlertEventOut,
    AlertRuleCreate,
    AlertRuleOut,
    AlertRuleUpdate,
)
from app.services import alert_store, redis_store

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/rules", response_model=List[AlertRuleOut])
def get_rules(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[AlertRuleOut]:
    return [alert_store.rule_to_out(r) for r in alert_store.list_rules(db, user)]


@router.post("/rules", response_model=AlertRuleOut, status_code=201)
def create_rule(
    body: AlertRuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AlertRuleOut:
    rule = alert_store.create_rule(
        db,
        user,
        ticker=body.ticker,
        alert_type=body.alert_type,
        threshold=body.threshold,
        cooldown_minutes=body.cooldown_minutes,
        note=body.note,
        enabled=body.enabled,
    )
    return alert_store.rule_to_out(rule)


@router.patch("/rules/{rule_id}", response_model=AlertRuleOut)
def patch_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AlertRuleOut:
    rule = alert_store.get_rule(db, user, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found.")
    updated = alert_store.update_rule(
        db,
        rule,
        threshold=body.threshold,
        cooldown_minutes=body.cooldown_minutes,
        note=body.note,
        enabled=body.enabled,
    )
    return alert_store.rule_to_out(updated)


@router.delete("/rules/{rule_id}")
def remove_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not alert_store.delete_rule(db, user, rule_id):
        raise HTTPException(status_code=404, detail="Alert rule not found.")
    return {"status": "removed", "id": rule_id}


@router.get("/events", response_model=List[AlertEventOut])
def get_events(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[AlertEventOut]:
    rows = alert_store.list_events(db, user, unread_only=unread_only, limit=limit)
    return [alert_store.event_to_out(e) for e in rows]


@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"count": alert_store.unread_count(db, user)}


@router.post("/events/{event_id}/read", response_model=AlertEventOut)
def read_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AlertEventOut:
    event = alert_store.mark_event_read(db, user, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Alert event not found.")
    return alert_store.event_to_out(event)


@router.post("/events/read-all")
def read_all_events(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    n = alert_store.mark_all_read(db, user)
    return {"marked": n}


@router.post("/evaluate", response_model=AlertEvaluateResponse)
async def evaluate_now(
    user: User = Depends(get_current_user),
) -> AlertEvaluateResponse:
    result = await run_alert_evaluation(user_id=user.id)
    return AlertEvaluateResponse(
        evaluated_rules=int(result.get("evaluated_rules") or 0),
        triggered=int(result.get("triggered") or 0),
        redis_mode=str(result.get("redis_mode") or redis_store.redis_mode()),
        events=result.get("events") or [],
    )

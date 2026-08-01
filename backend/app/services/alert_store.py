"""Alert rule/event persistence and evaluation helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, AlertRule, User
from app.models.alert_schemas import AlertEventOut, AlertRuleOut


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def rule_to_out(rule: AlertRule) -> AlertRuleOut:
    return AlertRuleOut(
        id=rule.id,
        ticker=rule.ticker,
        alert_type=rule.alert_type,
        threshold=float(rule.threshold),
        enabled=bool(rule.enabled),
        cooldown_minutes=int(rule.cooldown_minutes or 60),
        last_triggered_at=_iso(rule.last_triggered_at),
        created_at=_iso(rule.created_at) or datetime.now(timezone.utc).isoformat(),
        note=rule.note or "",
    )


def event_to_out(event: AlertEvent) -> AlertEventOut:
    return AlertEventOut(
        id=event.id,
        rule_id=event.rule_id,
        ticker=event.ticker,
        alert_type=event.alert_type,
        title=event.title,
        message=event.message,
        observed_value=event.observed_value,
        threshold=event.threshold,
        created_at=_iso(event.created_at) or datetime.now(timezone.utc).isoformat(),
        read_at=_iso(event.read_at),
        is_read=event.read_at is not None,
    )


def list_rules(db: Session, user: User) -> List[AlertRule]:
    return list(
        db.scalars(
            select(AlertRule)
            .where(AlertRule.user_id == user.id)
            .order_by(AlertRule.created_at.desc())
        ).all()
    )


def get_rule(db: Session, user: User, rule_id: int) -> Optional[AlertRule]:
    return db.scalar(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user.id)
    )


def create_rule(
    db: Session,
    user: User,
    *,
    ticker: str,
    alert_type: str,
    threshold: float,
    cooldown_minutes: int = 60,
    note: str = "",
    enabled: bool = True,
) -> AlertRule:
    rule = AlertRule(
        user_id=user.id,
        ticker=ticker.upper().strip(),
        alert_type=alert_type,
        threshold=threshold,
        cooldown_minutes=cooldown_minutes,
        note=note or "",
        enabled=enabled,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, user: User, rule_id: int) -> bool:
    rule = get_rule(db, user, rule_id)
    if rule is None:
        return False
    db.delete(rule)
    db.commit()
    return True


def update_rule(db: Session, rule: AlertRule, **fields) -> AlertRule:
    for key, value in fields.items():
        if value is not None and hasattr(rule, key):
            setattr(rule, key, value)
    db.commit()
    db.refresh(rule)
    return rule


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
    from sqlalchemy import func

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


def in_cooldown(rule: AlertRule, now: Optional[datetime] = None) -> bool:
    if rule.last_triggered_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    last = rule.last_triggered_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now < last + timedelta(minutes=int(rule.cooldown_minutes or 60))


def record_trigger(
    db: Session,
    rule: AlertRule,
    *,
    title: str,
    message: str,
    observed_value: Optional[float],
) -> AlertEvent:
    now = datetime.now(timezone.utc)
    event = AlertEvent(
        user_id=rule.user_id,
        rule_id=rule.id,
        ticker=rule.ticker,
        alert_type=rule.alert_type,
        title=title,
        message=message,
        observed_value=observed_value,
        threshold=float(rule.threshold),
        created_at=now,
    )
    rule.last_triggered_at = now
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_enabled_rules(db: Session) -> List[AlertRule]:
    return list(
        db.scalars(select(AlertRule).where(AlertRule.enabled.is_(True))).all()
    )

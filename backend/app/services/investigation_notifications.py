"""PRD v3 Phase 12 — notify users when material investigations complete."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, User
from app.models.alert_schemas import AlertEventOut
from app.models.investigation_schemas import InvestigationDetail
from app.observability.logger import get_logger, log_event
from app.services import alert_store, materiality
from app.services.autonomous_monitor import decide_notify, log_monitor
from app.services.move_detector import MoveSnapshot

logger = get_logger("investigation_notifications")

NOTIFY_ENABLED = os.getenv("INVESTIGATION_NOTIFY_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PROACTIVE_REASONS: Set[str] = {"scheduled", "price_move", "alert"}
NOTIFY_COOLDOWN_HOURS = float(os.getenv("INVESTIGATION_NOTIFY_COOLDOWN_HOURS", "6"))


def maybe_notify_investigation(
    db: Session,
    *,
    user: Optional[User],
    detail: InvestigationDetail,
    move: MoveSnapshot,
) -> Optional[AlertEventOut]:
    """
    Create an in-app AlertEvent when a proactive investigation clears the
    materiality bar. Returns the event or None if skipped.
    """
    recent_note = False
    if user is not None:
        recent_note = _recent_notification_exists(
            db, user_id=user.id, ticker=detail.ticker, within_hours=NOTIFY_COOLDOWN_HOURS
        )
    trend = ""
    if user is not None:
        try:
            from app.services import company_memory

            summary = company_memory.build_summary(
                db, owner_key=f"user:{user.id}", ticker=detail.ticker
            )
            trend = (summary.management_trend or "").strip()
        except Exception:
            trend = ""
    decision = decide_notify(
        status=detail.status,
        trigger_reason=detail.trigger_reason,
        claims=detail.claims or [],
        move=move,
        notify_enabled=NOTIFY_ENABLED,
        user_present=user is not None,
        recent_notification=recent_note,
        memory_trend=trend,
    )
    if not decision.should_notify:
        log_monitor(
            "MONITOR_NOTIFY_SUPPRESSED",
            ticker=detail.ticker,
            investigation_id=detail.id,
            status=detail.status,
            reason=decision.reason,
        )
        log_event(
            logger,
            logging.INFO,
            "NOTIFICATION_SUPPRESSED",
            event="NOTIFICATION_SUPPRESSED",
            ticker=detail.ticker,
            investigation_id=detail.id,
            reason=decision.reason,
            score=decision.score,
        )
        return None

    leading = None
    for claim in detail.claims or []:
        if leading is None or claim.rank < leading.rank:
            leading = claim

    lead_text = (leading.statement if leading else detail.summary or "").strip()
    if len(lead_text) > 280:
        lead_text = lead_text[:277] + "…"

    title = f"{detail.ticker}: material move investigated"
    move_bit = (
        f"{detail.move_pct:+.1f}%" if detail.move_pct is not None else "move"
    )
    memory_bit = f" {decision.extra_note}" if decision.extra_note else ""
    message = (
        f"{move_bit} cleared the materiality bar (score {decision.score:.0f}). "
        f"{lead_text or 'Open the Evidence Ledger for ranked claims.'}"
        f"{memory_bit}"
    )

    event = AlertEvent(
        user_id=user.id,
        rule_id=None,
        ticker=detail.ticker.upper().strip(),
        alert_type="investigation_material",
        title=title[:280],
        message=message,
        observed_value=decision.score,
        threshold=materiality.NOTIFY_MIN_SCORE,
        investigation_id=detail.id,
        materiality_score=decision.score,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    log_event(
        logger,
        logging.INFO,
        "NOTIFICATION_SENT",
        event="NOTIFICATION_SENT",
        ticker=detail.ticker,
        investigation_id=detail.id,
        materiality_score=decision.score,
        event_id=event.id,
    )
    log_monitor(
        "MONITOR_NOTIFY",
        ticker=detail.ticker,
        investigation_id=detail.id,
        status="notified",
        relevance=decision.score,
    )

    _maybe_email_stub(user_email=user.email, title=title, message=message)
    return alert_store.event_to_out(event)


def _recent_notification_exists(
    db: Session,
    *,
    user_id: int,
    ticker: str,
    within_hours: float,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.1, within_hours))
    row = db.scalar(
        select(AlertEvent.id)
        .where(
            AlertEvent.user_id == user_id,
            AlertEvent.ticker == ticker.upper().strip(),
            AlertEvent.alert_type == "investigation_material",
            AlertEvent.created_at >= cutoff,
        )
        .limit(1)
    )
    return row is not None


def _maybe_email_stub(*, user_email: str, title: str, message: str) -> None:
    """Optional email — only logs unless SMTP is configured later."""
    if os.getenv("ALERT_EMAIL_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    log_event(
        logger,
        logging.INFO,
        "ALERT_EMAIL_ENABLED set but SMTP delivery not configured; in-app only",
        email=user_email,
        title=title,
        message=message[:120],
    )

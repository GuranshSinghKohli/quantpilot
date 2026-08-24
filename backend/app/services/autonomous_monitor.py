"""Phase 9 — autonomous monitor decisions on top of the existing gate.

Does not replace event detection, materiality, LangGraph, or notify.
Answers: investigate again? reopen? notify?
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.config import INVESTIGATION_REOPEN_MOVE_DELTA
from app.db.models import Investigation
from app.models.event_schemas import MarketEvent
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

logger = get_logger("autonomous_monitor")

IN_FLIGHT = {"planning", "collecting", "verifying"}
PRICE_FAMILY = {
    "PRICE_MOVE",
    "IDIOSYNCRATIC_MOVE",
    "VOLATILITY_SPIKE",
    "VOLUME_SPIKE",
}


@dataclass
class ReopenDecision:
    should_reopen: bool
    reason: str
    change_kind: str = "none"
    prior_investigation_id: Optional[int] = None
    in_flight: bool = False


@dataclass
class NotifyDecision:
    should_notify: bool
    reason: str
    score: float = 0.0
    extra_note: str = ""


def event_family(event_type: Optional[str]) -> str:
    kind = (event_type or "").upper()
    if kind in PRICE_FAMILY:
        return "price"
    if kind == "FILING":
        return "filing"
    if kind == "EARNINGS":
        return "earnings"
    if kind == "NEWS":
        return "news"
    if kind == "MACRO":
        return "macro"
    return kind.lower() or "unknown"


def last_event_type(investigation: Optional[Investigation]) -> Optional[str]:
    if investigation is None:
        return None
    try:
        roster = json.loads(investigation.roster_json or "{}")
    except Exception:
        roster = {}
    graph = roster.get("investigation_graph") if isinstance(roster, dict) else {}
    event = graph.get("event") if isinstance(graph, dict) else {}
    if isinstance(event, dict) and event.get("event_type"):
        return str(event["event_type"])
    if investigation.move_pct is not None:
        return "PRICE_MOVE"
    return None


def decide_reopen(
    *,
    last: Optional[Investigation],
    current_event: Optional[MarketEvent] = None,
    current_move_pct: Optional[float] = None,
    memory_changed: bool = False,
    move_delta: Optional[float] = None,
) -> ReopenDecision:
    """Reopen only when the new event is materially different from the last case."""
    if last is None:
        return ReopenDecision(False, "No prior investigation.", change_kind="none")
    prior_id = last.id
    if last.status in IN_FLIGHT:
        return ReopenDecision(
            False,
            f"Prior investigation #{prior_id} is still {last.status}.",
            change_kind="in_flight",
            prior_investigation_id=prior_id,
            in_flight=True,
        )

    reasons = []
    kind = "none"
    last_type = last_event_type(last)
    cur_type = current_event.event_type.value if current_event is not None else None
    if last_type and cur_type and event_family(last_type) != event_family(cur_type):
        reasons.append(f"new event type {cur_type} (prior {last_type})")
        kind = "event_type"

    last_m = last.move_pct
    cur_m = current_move_pct
    if cur_m is None and current_event is not None:
        cur_m = current_event.magnitude
    delta = INVESTIGATION_REOPEN_MOVE_DELTA if move_delta is None else move_delta
    if last_m is not None and cur_m is not None:
        if abs(cur_m) - abs(last_m) >= delta:
            reasons.append(f"move enlarged {last_m:+.1f}% → {cur_m:+.1f}%")
            kind = "magnitude"
        if last_m * cur_m < 0 and abs(last_m) >= 2.0 and abs(cur_m) >= 2.0:
            reasons.append("direction reversed")
            kind = "direction"

    if last.status in {"failed", "skipped_market_noise"} and current_event is not None:
        cur = current_event.event_type.value
        if event_family(cur) in {"filing", "earnings"}:
            reasons.append(f"prior case {last.status}; new {cur} disclosure")
            kind = "failed_prior"

    if memory_changed:
        reasons.append("company memory conflict since the last investigation")
        kind = "memory"

    if not reasons:
        return ReopenDecision(
            False,
            "Recent investigation still covers this event; no material change.",
            change_kind="none",
            prior_investigation_id=prior_id,
        )
    return ReopenDecision(
        True,
        "Reopening because " + "; ".join(reasons) + ".",
        change_kind=kind,
        prior_investigation_id=prior_id,
    )


def memory_changed_since(
    db: Optional[Session],
    *,
    owner_key: str,
    ticker: str,
    since: Optional[datetime],
) -> bool:
    if db is None or not owner_key:
        return False
    try:
        from app.services import company_memory

        items = company_memory._load_active(
            db, owner_key, ticker, include_outdated=True
        )
    except Exception:
        return False
    for item in items:
        if item.status != "OUTDATED":
            continue
        if item.memory_type not in {"MANAGEMENT_STATEMENT", "RISK"}:
            continue
        seen = item.last_seen_at or item.created_at
        try:
            if since is None or (seen is not None and seen >= since):
                return True
        except TypeError:
            return True
    return False


def decide_notify(
    *,
    status: str,
    trigger_reason: str,
    claims: Sequence[Any],
    move: MoveSnapshot,
    notify_enabled: bool = True,
    user_present: bool = True,
    recent_notification: bool = False,
    memory_trend: str = "",
) -> NotifyDecision:
    """Post-investigation notify. Materiality still wins. No trading advice."""
    if not notify_enabled:
        return NotifyDecision(False, "Notifications disabled.")
    if not user_present:
        return NotifyDecision(False, "No signed-in user to notify.")
    if trigger_reason not in {"scheduled", "price_move", "alert"}:  # proactive only
        return NotifyDecision(False, "On-demand investigations do not notify.")
    if status != "complete":
        return NotifyDecision(False, f"Investigation status {status} is not complete.")
    if not claims:
        return NotifyDecision(False, "No grounded claims; suppress notification.")

    leading = None
    for claim in claims:
        rank = getattr(claim, "rank", None)
        if leading is None or (rank is not None and rank < getattr(leading, "rank", 99)):
            leading = claim
    stance = str(getattr(leading, "stance", "") or "").lower()
    if stance == "market_noise":
        return NotifyDecision(False, "Leading claim is market noise; suppress notification.")

    from app.services import materiality

    assessment = materiality.assess_materiality(move)
    if not assessment.should_notify:
        return NotifyDecision(
            False, assessment.reason, score=assessment.score
        )
    if recent_notification:
        return NotifyDecision(
            False, "Notification cooldown is active.", score=assessment.score
        )
    note = (memory_trend or "").strip()
    return NotifyDecision(
        True,
        assessment.reason,
        score=assessment.score,
        extra_note=note,
    )


def log_monitor(event: str, **extra: Any) -> None:
    log_event(logger, logging.INFO, event, event=event, **extra)

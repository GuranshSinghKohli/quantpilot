"""Phase 2 — pre-investigation gate: detect → score → cooldown → decide."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.config import (
    INVESTIGATION_COOLDOWN_HOURS,
    INVESTIGATION_EVENT_BUCKET_HOURS,
    INVESTIGATION_MATERIALITY_THRESHOLD,
)
from app.models.event_schemas import InvestigationGateDecision, MarketEvent
from app.observability.logger import get_logger, log_event
from app.services import evidence_ledger_store, materiality, redis_store
from app.services.autonomous_monitor import decide_reopen, log_monitor, memory_changed_since
from app.services.event_detection import detect_events
from app.services.move_detector import MoveSnapshot
from app.services.portfolio_exposure import portfolio_weight
from app.services.trigger_logic import TriggerDecision, evaluate_trigger

logger = get_logger("investigation_gate")


def _time_bucket(now: Optional[datetime] = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    hours = max(1, INVESTIGATION_EVENT_BUCKET_HOURS)
    slot = stamp.hour // hours
    return f"{stamp.date().isoformat()}-b{slot}"


def event_fingerprint_key(
    owner_key: str,
    event: MarketEvent,
    *,
    bucket: Optional[str] = None,
) -> str:
    return (
        f"quantpilot:event_fp:{owner_key}:"
        f"{event.fingerprint(bucket=bucket or _time_bucket())}"
    )


def fingerprint_seen(owner_key: str, event: MarketEvent) -> bool:
    return bool(redis_store.cache_get(event_fingerprint_key(owner_key, event)))


def remember_fingerprint(owner_key: str, event: MarketEvent) -> None:
    ttl = int(max(1.0, INVESTIGATION_COOLDOWN_HOURS) * 3600)
    redis_store.cache_set(
        event_fingerprint_key(owner_key, event),
        {"ticker": event.ticker, "event_type": event.event_type.value},
        ttl_seconds=ttl,
    )


def _decision(
    trigger: TriggerDecision,
    *,
    events: List[MarketEvent],
    exposure: float,
    should: bool,
    reason: str,
    cooldown: bool = False,
    primary: Optional[MarketEvent] = None,
    scored=None,
    reopen: bool = False,
    reopen_reason: str = "",
    prior_investigation_id: Optional[int] = None,
) -> InvestigationGateDecision:
    move = attach_move_from_events(trigger, primary, scored)
    return InvestigationGateDecision(
        ticker=trigger.move.ticker,
        should_investigate=should,
        reason=reason,
        depth=(scored.depth if scored is not None else trigger.depth) or "standard",
        score=float(scored.score) if scored is not None else 0.0,
        exposure=exposure,
        cooldown=cooldown,
        reopen=reopen,
        reopen_reason=reopen_reason,
        prior_investigation_id=prior_investigation_id,
        events=events,
        primary_event=primary,
        materiality=scored,
        window_label=trigger.move.window_label,
        move_pct=trigger.move.move_pct,
        residual_pct=trigger.residual_pct,
        move_zscore=trigger.move_zscore,
        benchmark_move_pct=trigger.benchmark_move_pct,
        realized_vol_pct=trigger.realized_vol_pct,
        asset_class=trigger.asset_class,
        move=move,
    )


def apply_exposure(events: List[MarketEvent], exposure: float) -> List[MarketEvent]:
    for ev in events:
        ev.portfolio_exposure = exposure
    return events


def attach_move_from_events(
    trigger: TriggerDecision,
    primary: Optional[MarketEvent],
    materiality_result,
) -> MoveSnapshot:
    """Copy gate depth onto the existing move snapshot for the runner."""
    move = trigger.move
    if materiality_result is not None:
        move.depth = materiality_result.depth
        move.should_investigate = bool(materiality_result.should_investigate)
        move.skip_reason = (
            "" if materiality_result.should_investigate else materiality_result.reason
        )
    if primary is not None and primary.magnitude is not None and move.move_pct is None:
        move.move_pct = primary.magnitude
    return move


async def evaluate_investigation_gate(
    ticker: str,
    *,
    owner_key: Optional[str] = None,
    db: Optional[Session] = None,
    holdings: Optional[Iterable[Any]] = None,
    window: str = "1d",
    mark_prices: Optional[dict] = None,
    persist_fingerprint: bool = False,
) -> InvestigationGateDecision:
    """
    Detect events, score pre-investigation materiality, apply cooldown.

    Does not launch an investigation.
    """
    symbol = ticker.upper().strip()
    trigger = await evaluate_trigger(symbol, window=window)
    events = await detect_events(symbol, window=window, trigger=trigger)
    marks = dict(mark_prices or {})
    if trigger.move.current_price:
        marks[symbol] = float(trigger.move.current_price)
    exposure = portfolio_weight(symbol, holdings or [], mark_prices=marks)
    apply_exposure(events, exposure)

    primary = materiality.pick_primary_event(events)
    last = None
    if owner_key and db is not None:
        last = evidence_ledger_store.latest_investigation(
            db, owner_key=owner_key, ticker=symbol
        )

    if primary is None:
        log_event(
            logger,
            logging.INFO,
            "INVESTIGATION_GATE_FAIL",
            event="INVESTIGATION_GATE_FAIL",
            ticker=symbol,
            reason="no detectable event",
        )
        return _decision(
            trigger,
            events=events,
            exposure=exposure,
            should=False,
            reason=f"Skipped {symbol}: no detectable price, filing, or earnings event.",
        )

    reopen_flag = False
    reopen_reason = ""
    prior_id = last.id if last is not None else None
    recent = bool(
        owner_key
        and db is not None
        and evidence_ledger_store.recent_investigation_exists(
            db,
            owner_key=owner_key,
            ticker=symbol,
            within_hours=INVESTIGATION_COOLDOWN_HOURS,
        )
    )
    fingerprint_hit = bool(owner_key and fingerprint_seen(owner_key, primary))
    if recent or fingerprint_hit:
        changed = memory_changed_since(
            db,
            owner_key=owner_key or "",
            ticker=symbol,
            since=getattr(last, "completed_at", None) or getattr(last, "created_at", None),
        )
        reopen = decide_reopen(
            last=last,
            current_event=primary,
            current_move_pct=primary.magnitude if primary.magnitude is not None else trigger.move.move_pct,
            memory_changed=changed,
        )
        if not reopen.should_reopen:
            log_monitor(
                "MONITOR_COOLDOWN",
                ticker=symbol,
                investigation_id=reopen.prior_investigation_id,
                status="cooldown",
                reason=reopen.reason,
            )
            log_event(
                logger,
                logging.INFO,
                "EVENT_COOLDOWN",
                event="EVENT_COOLDOWN",
                ticker=symbol,
                event_type=primary.event_type.value,
                reason=reopen.reason or "cooldown",
                hours=INVESTIGATION_COOLDOWN_HOURS,
            )
            return _decision(
                trigger,
                events=events,
                exposure=exposure,
                should=False,
                reason=(
                    reopen.reason
                    or (
                        f"Skipped {symbol}: investigation cooldown "
                        f"({INVESTIGATION_COOLDOWN_HOURS:g}h) is active."
                    )
                ),
                cooldown=True,
                primary=primary,
                reopen=False,
                reopen_reason=reopen.reason,
                prior_investigation_id=reopen.prior_investigation_id,
            )
        reopen_flag = True
        reopen_reason = reopen.reason
        prior_id = reopen.prior_investigation_id
        log_monitor(
            "MONITOR_REOPEN",
            ticker=symbol,
            investigation_id=reopen.prior_investigation_id,
            status="reopen",
            reason=reopen.reason,
            memory_type=reopen.change_kind,
        )

    scored = materiality.assess_investigation_materiality(
        primary,
        threshold=INVESTIGATION_MATERIALITY_THRESHOLD,
        exposure=exposure,
    )
    attach_move_from_events(trigger, primary, scored)

    if scored.should_investigate:
        log_event(
            logger,
            logging.INFO,
            "INVESTIGATION_GATE_PASS",
            event="INVESTIGATION_GATE_PASS",
            ticker=symbol,
            event_type=primary.event_type.value,
            materiality_score=scored.score,
            portfolio_exposure=exposure,
            depth=scored.depth,
            reason=scored.reason,
        )
        if persist_fingerprint and owner_key:
            remember_fingerprint(owner_key, primary)
    else:
        log_event(
            logger,
            logging.INFO,
            "INVESTIGATION_GATE_FAIL",
            event="INVESTIGATION_GATE_FAIL",
            ticker=symbol,
            event_type=primary.event_type.value,
            materiality_score=scored.score,
            portfolio_exposure=exposure,
            reason=scored.reason,
        )

    return _decision(
        trigger,
        events=events,
        exposure=exposure,
        should=scored.should_investigate,
        reason=(
            f"{reopen_reason} {scored.reason}".strip()
            if reopen_flag
            else scored.reason
        ),
        primary=primary,
        scored=scored,
        reopen=reopen_flag,
        reopen_reason=reopen_reason,
        prior_investigation_id=prior_id,
    )

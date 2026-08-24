"""PRD v3 Phase 11 — scheduled portfolio investigation sweep."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import INVESTIGATION_SWEEP_MAX_PER_RUN, SWEEP_LOCK_TTL_SECONDS
from app.db.models import User
from app.db.session import SessionLocal
from app.jobs.briefing_job import list_users_with_holdings
from app.observability.logger import get_logger, log_event
from app.services import evidence_ledger_store, redis_store, watchlist_store
from app.services.autonomous_monitor import IN_FLIGHT, log_monitor
from app.services.investigation_gate import evaluate_investigation_gate
from app.services.investigation_runner import investigate_ticker
from app.services.redis_store import INVESTIGATION_QUEUE_KEY

logger = get_logger("investigation_sweep")


async def run_investigation_sweep(
    *,
    user: Optional[User] = None,
    max_launches: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Evaluate holdings with the Phase 2 investigation gate, then launch.

    If user is provided, only that user's holdings are scanned (manual API).
    Otherwise all users with holdings are scanned (scheduler).
    """
    db = SessionLocal()
    launched = 0
    skipped_cooldown = 0
    skipped_trigger = 0
    skipped_materiality = 0
    reopened = 0
    skipped_unchanged = 0
    errors = 0
    evaluated = 0
    details: List[Dict[str, Any]] = []
    limit = max(1, min(50, max_launches or INVESTIGATION_SWEEP_MAX_PER_RUN))
    if not redis_store.acquire_lock("investigation_sweep", ttl_seconds=SWEEP_LOCK_TTL_SECONDS):
        db.close()
        log_event(
            logger,
            logging.INFO,
            "MONITORING_SWEEP_SKIPPED",
            event="MONITORING_SWEEP_SKIPPED",
            reason="lock_held",
        )
        return {
            "evaluated": 0,
            "launched": 0,
            "skipped_cooldown": 0,
            "skipped_trigger": 0,
            "skipped_materiality": 0,
            "reopened": 0,
            "skipped_unchanged": 0,
            "errors": 0,
            "dry_run": dry_run,
            "details": [{"action": "skipped", "reason": "lock_held"}],
        }
    log_monitor("MONITOR_STARTED", status="start", dry_run=dry_run)
    log_event(
        logger,
        logging.INFO,
        "MONITORING_SWEEP_STARTED",
        event="MONITORING_SWEEP_STARTED",
        dry_run=dry_run,
    )

    try:
        users = [user] if user is not None else list_users_with_holdings(db)
        for u in users:
            if launched >= limit:
                break
            owner_key = f"user:{u.id}"
            entries = watchlist_store.list_watchlist(db, u)
            tickers = sorted({e.ticker.upper() for e in entries if e.ticker})
            for ticker in tickers:
                if launched >= limit:
                    break
                evaluated += 1
                try:
                    last = evidence_ledger_store.latest_investigation(
                        db, owner_key=owner_key, ticker=ticker
                    )
                    if last is not None and last.status in IN_FLIGHT:
                        skipped_cooldown += 1
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "cooldown",
                                "reason": f"investigation #{last.id} is {last.status}",
                            }
                        )
                        log_monitor(
                            "MONITOR_COOLDOWN",
                            ticker=ticker,
                            investigation_id=last.id,
                            status=last.status,
                        )
                        continue

                    gate = await evaluate_investigation_gate(
                        ticker,
                        owner_key=owner_key,
                        db=db,
                        holdings=entries,
                        window="1d",
                        persist_fingerprint=not dry_run,
                    )
                    if gate.cooldown:
                        skipped_cooldown += 1
                        skipped_unchanged += 1
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "cooldown",
                                "reason": gate.reason,
                                "event_type": (
                                    gate.primary_event.event_type.value
                                    if gate.primary_event
                                    else None
                                ),
                            }
                        )
                        continue
                    if gate.reopen:
                        reopened += 1
                    if not gate.should_investigate:
                        skipped_materiality += 1
                        skipped_trigger += 1
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "skip_materiality",
                                "reason": gate.reason,
                                "score": gate.score,
                                "exposure": gate.exposure,
                                "event_type": (
                                    gate.primary_event.event_type.value
                                    if gate.primary_event
                                    else None
                                ),
                            }
                        )
                        continue

                    if dry_run:
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "would_launch",
                                "reason": gate.reason,
                                "depth": gate.depth,
                                "asset_class": gate.asset_class,
                                "score": gate.score,
                                "exposure": gate.exposure,
                                "event_type": (
                                    gate.primary_event.event_type.value
                                    if gate.primary_event
                                    else None
                                ),
                            }
                        )
                        launched += 1
                        continue

                    inv = await investigate_ticker(
                        db,
                        owner_key=owner_key,
                        user=u,
                        ticker=ticker,
                        trigger_reason="scheduled",
                        window_label="1d",
                        use_trigger_gate=False,
                        move_snapshot=gate.move,
                    )
                    launched += 1
                    details.append(
                        {
                            "user_id": u.id,
                            "ticker": ticker,
                            "action": "launched",
                            "investigation_id": inv.id,
                            "status": inv.status,
                            "depth": gate.depth,
                            "asset_class": gate.asset_class,
                            "score": gate.score,
                            "exposure": gate.exposure,
                            "reopen": gate.reopen,
                            "event_type": (
                                gate.primary_event.event_type.value
                                if gate.primary_event
                                else None
                            ),
                        }
                    )
                    log_monitor(
                        "MONITOR_LAUNCH",
                        ticker=ticker,
                        investigation_id=inv.id,
                        status=inv.status,
                        reopen=gate.reopen,
                    )
                except Exception as exc:
                    errors += 1
                    details.append(
                        {
                            "user_id": u.id,
                            "ticker": ticker,
                            "action": "error",
                            "error": str(exc)[:240],
                        }
                    )
                    log_event(
                        logger,
                        logging.ERROR,
                        "Sweep ticker failed",
                        user_id=u.id,
                        ticker=ticker,
                        error=str(exc),
                    )
    finally:
        db.close()
        redis_store.release_lock("investigation_sweep")

    summary = {
        "evaluated": evaluated,
        "launched": launched,
        "skipped_cooldown": skipped_cooldown,
        "skipped_trigger": skipped_trigger,
        "skipped_materiality": skipped_materiality,
        "reopened": reopened,
        "skipped_unchanged": skipped_unchanged,
        "errors": errors,
        "dry_run": dry_run,
        "details": details[:100],
    }
    log_event(
        logger,
        logging.INFO,
        "MONITORING_SWEEP_COMPLETED",
        event="MONITORING_SWEEP_COMPLETED",
        **{k: v for k, v in summary.items() if k != "details"},
    )
    log_monitor(
        "MONITOR_COMPLETED",
        status="complete",
        relevance=launched,
        **{k: v for k, v in summary.items() if k != "details"},
    )
    return summary


def enqueue_investigation_sweep(
    *,
    user_id: Optional[int] = None,
    max_launches: Optional[int] = None,
    dry_run: bool = False,
) -> None:
    """PRD v3 Phase 12 — push sweep onto Redis/memory job queue."""
    redis_store.queue_push(
        {
            "job": "investigation_sweep",
            "user_id": user_id,
            "max_launches": max_launches,
            "dry_run": dry_run,
        },
        queue_key=INVESTIGATION_QUEUE_KEY,
    )


async def process_investigation_queue(max_jobs: int = 5) -> int:
    """Drain investigation jobs (sweep). Returns jobs processed."""
    processed = 0
    for _ in range(max_jobs):
        payload = redis_store.queue_pop(queue_key=INVESTIGATION_QUEUE_KEY)
        if not payload:
            break
        if payload.get("job") != "investigation_sweep":
            continue
        user = None
        user_id = payload.get("user_id")
        if user_id is not None:
            db = SessionLocal()
            try:
                user = db.scalar(select(User).where(User.id == int(user_id)))
            finally:
                db.close()
        await run_investigation_sweep(
            user=user,
            max_launches=payload.get("max_launches"),
            dry_run=bool(payload.get("dry_run")),
        )
        processed += 1
    return processed


def run_investigation_sweep_sync() -> Dict[str, Any]:
    """APScheduler entrypoint: enqueue then drain (Redis-backed jobs)."""

    async def _run() -> Dict[str, Any]:
        enqueue_investigation_sweep()
        drained = await process_investigation_queue()
        if drained == 0:
            return await run_investigation_sweep()
        return {
            "queued_drained": drained,
            "redis_mode": redis_store.redis_mode(),
        }

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_run())).result()

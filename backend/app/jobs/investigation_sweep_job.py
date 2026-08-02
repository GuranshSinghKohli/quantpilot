"""PRD v3 Phase 11 — scheduled portfolio investigation sweep."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    INVESTIGATION_COOLDOWN_HOURS,
    INVESTIGATION_SWEEP_MAX_PER_RUN,
)
from app.db.models import User
from app.db.session import SessionLocal
from app.jobs.briefing_job import list_users_with_holdings
from app.observability.logger import get_logger, log_event
from app.services import evidence_ledger_store, redis_store, trigger_logic, watchlist_store
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
    Evaluate holdings with Phase 11 trigger logic and launch investigations.

    If user is provided, only that user's holdings are scanned (manual API).
    Otherwise all users with holdings are scanned (scheduler).
    """
    db = SessionLocal()
    launched = 0
    skipped_cooldown = 0
    skipped_trigger = 0
    errors = 0
    evaluated = 0
    details: List[Dict[str, Any]] = []
    limit = max(1, min(50, max_launches or INVESTIGATION_SWEEP_MAX_PER_RUN))

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
                    if evidence_ledger_store.recent_investigation_exists(
                        db,
                        owner_key=owner_key,
                        ticker=ticker,
                        within_hours=INVESTIGATION_COOLDOWN_HOURS,
                    ):
                        skipped_cooldown += 1
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "cooldown",
                            }
                        )
                        continue

                    decision = await trigger_logic.evaluate_trigger(ticker)
                    if not decision.should_investigate:
                        skipped_trigger += 1
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "skip_trigger",
                                "reason": decision.reason,
                            }
                        )
                        continue

                    if dry_run:
                        details.append(
                            {
                                "user_id": u.id,
                                "ticker": ticker,
                                "action": "would_launch",
                                "reason": decision.reason,
                                "depth": decision.depth,
                                "asset_class": decision.asset_class,
                            }
                        )
                        launched += 1
                        continue

                    # Trigger already passed; reuse move metrics for materiality notify.
                    inv = await investigate_ticker(
                        db,
                        owner_key=owner_key,
                        user=u,
                        ticker=ticker,
                        trigger_reason="scheduled",
                        window_label="1d",
                        use_trigger_gate=False,
                        move_snapshot=decision.move,
                    )
                    launched += 1
                    details.append(
                        {
                            "user_id": u.id,
                            "ticker": ticker,
                            "action": "launched",
                            "investigation_id": inv.id,
                            "status": inv.status,
                            "depth": decision.depth,
                            "asset_class": decision.asset_class,
                        }
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

    summary = {
        "evaluated": evaluated,
        "launched": launched,
        "skipped_cooldown": skipped_cooldown,
        "skipped_trigger": skipped_trigger,
        "errors": errors,
        "dry_run": dry_run,
        "details": details[:100],
    }
    log_event(
        logger,
        logging.INFO,
        "Investigation sweep complete",
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

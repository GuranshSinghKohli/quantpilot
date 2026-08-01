"""Alert evaluation job + Redis queue worker (Phase 9)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.observability.logger import get_logger, log_event
from app.services import alert_engine, redis_store

logger = get_logger("alert_job")


async def run_alert_evaluation(user_id: Optional[int] = None) -> Dict[str, Any]:
    result = await alert_engine.evaluate_all_enabled_rules(user_id=user_id)
    log_event(
        logger,
        logging.INFO,
        "Alert evaluation finished",
        evaluated_rules=result.get("evaluated_rules"),
        triggered=result.get("triggered"),
        redis_mode=result.get("redis_mode"),
        user_id=user_id,
    )
    return result


def enqueue_alert_evaluation() -> None:
    redis_store.queue_push({"job": "evaluate_alerts"})


async def process_alert_queue(max_jobs: int = 10) -> int:
    processed = 0
    for _ in range(max_jobs):
        payload = redis_store.queue_pop()
        if not payload:
            break
        if payload.get("job") == "evaluate_alerts":
            await run_alert_evaluation()
            processed += 1
    return processed


def run_alert_evaluation_sync() -> None:
    """APScheduler entry: enqueue then drain (or run directly)."""
    try:
        enqueue_alert_evaluation()

        async def _run() -> None:
            drained = await process_alert_queue()
            if drained == 0:
                # nothing queued (race) - still evaluate once
                await run_alert_evaluation()

        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(_run())).result()
        except RuntimeError:
            asyncio.run(_run())
    except Exception as exc:
        log_event(logger, logging.ERROR, "Alert job failed", error=str(exc))

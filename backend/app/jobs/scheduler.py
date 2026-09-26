"""APScheduler wiring for investigation sweeps."""

from __future__ import annotations

import logging
import os
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    INVESTIGATION_SWEEP_INTERVAL_MINUTES,
    INVESTIGATIONS_SWEEP_ENABLED,
)
from app.jobs.investigation_sweep_job import run_investigation_sweep_sync
from app.observability.logger import get_logger, log_event

logger = get_logger("scheduler")

_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    lightweight = os.getenv("QUANTPILOT_LIGHTWEIGHT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    sweep_on = INVESTIGATIONS_SWEEP_ENABLED and not lightweight
    if not sweep_on:
        log_event(logger, logging.INFO, "All schedulers disabled")
        return None

    scheduler = BackgroundScheduler(timezone="UTC")

    if sweep_on:
        minutes = max(5, min(240, INVESTIGATION_SWEEP_INTERVAL_MINUTES))
        scheduler.add_job(
            run_investigation_sweep_sync,
            trigger=IntervalTrigger(minutes=minutes),
            id="investigation_move_sweep",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log_event(
            logger,
            logging.INFO,
            "Investigation sweep scheduler registered",
            interval_minutes=minutes,
        )

    scheduler.start()
    _scheduler = scheduler
    log_event(logger, logging.INFO, "Background scheduler started")
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            log_event(logger, logging.INFO, "Background scheduler stopped")
        except Exception as exc:
            log_event(logger, logging.WARNING, "Scheduler shutdown error", error=str(exc))
        _scheduler = None

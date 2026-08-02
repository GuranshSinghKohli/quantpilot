"""APScheduler wiring for briefings, smart alerts, and investigation sweeps."""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    ALERT_INTERVAL_MINUTES,
    ALERTS_ENABLED,
    BRIEFING_ENABLED,
    BRIEFING_HOUR_UTC,
    BRIEFING_MINUTE_UTC,
    INVESTIGATION_SWEEP_INTERVAL_MINUTES,
    INVESTIGATIONS_SWEEP_ENABLED,
)
from app.jobs.alert_job import run_alert_evaluation_sync
from app.jobs.briefing_job import run_daily_briefings_sync
from app.jobs.investigation_sweep_job import run_investigation_sweep_sync
from app.observability.logger import get_logger, log_event

logger = get_logger("scheduler")

_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    if not BRIEFING_ENABLED and not ALERTS_ENABLED and not INVESTIGATIONS_SWEEP_ENABLED:
        log_event(logger, logging.INFO, "All schedulers disabled")
        return None

    scheduler = BackgroundScheduler(timezone="UTC")

    if BRIEFING_ENABLED:
        scheduler.add_job(
            run_daily_briefings_sync,
            trigger=CronTrigger(
                hour=max(0, min(23, BRIEFING_HOUR_UTC)),
                minute=max(0, min(59, BRIEFING_MINUTE_UTC)),
            ),
            id="daily_portfolio_briefings",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log_event(
            logger,
            logging.INFO,
            "Briefing scheduler registered",
            hour_utc=BRIEFING_HOUR_UTC,
            minute_utc=BRIEFING_MINUTE_UTC,
        )

    if ALERTS_ENABLED:
        minutes = max(1, min(180, ALERT_INTERVAL_MINUTES))
        scheduler.add_job(
            run_alert_evaluation_sync,
            trigger=IntervalTrigger(minutes=minutes),
            id="smart_alert_evaluation",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log_event(
            logger,
            logging.INFO,
            "Alert scheduler registered",
            interval_minutes=minutes,
        )

    if INVESTIGATIONS_SWEEP_ENABLED:
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

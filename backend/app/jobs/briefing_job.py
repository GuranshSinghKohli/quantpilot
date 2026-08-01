"""Job runners for daily portfolio briefings."""

from __future__ import annotations

import asyncio
import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.agents.portfolio_agent import generate_portfolio_briefing
from app.db.models import Holding, Portfolio, User
from app.db.session import ANON_EMAIL, SessionLocal, get_or_create_default_portfolio
from app.models.agent_schemas import DailyBriefingResponse
from app.observability.logger import get_logger, log_event
from app.services import briefing_store, watchlist_store

logger = get_logger("briefing_job")


def list_users_with_holdings(db: Session) -> List[User]:
    rows = db.scalars(
        select(User)
        .join(Portfolio, Portfolio.user_id == User.id)
        .join(Holding, Holding.portfolio_id == Portfolio.id)
        .where(User.email != ANON_EMAIL)
        .options(joinedload(User.portfolios))
        .distinct()
    ).unique().all()
    return list(rows)


async def generate_briefing_for_user(
    db: Session,
    user: User,
) -> DailyBriefingResponse:
    portfolio = get_or_create_default_portfolio(db, user)
    entries = watchlist_store.list_watchlist(db, user)
    tickers = [e.ticker for e in entries]

    if not tickers:
        from app.models.agent_schemas import PortfolioAnalysis, PortfolioBriefingOutput

        output = PortfolioBriefingOutput(
            headline="Empty portfolio",
            summary="Add holdings to your watchlist/portfolio to receive daily briefings.",
            confidence_score=0.0,
        )
        row = briefing_store.save_briefing(
            db,
            user=user,
            portfolio=portfolio,
            output=output,
            analysis=PortfolioAnalysis(summary=output.summary),
            status="empty",
        )
        return briefing_store.briefing_to_response(row)

    output, analysis = await generate_portfolio_briefing(tickers)
    row = briefing_store.save_briefing(
        db,
        user=user,
        portfolio=portfolio,
        output=output,
        analysis=analysis,
    )
    log_event(
        logger,
        logging.INFO,
        "Portfolio briefing generated",
        user_id=user.id,
        holdings=len(tickers),
        briefing_id=row.id,
        status=row.status,
    )
    return briefing_store.briefing_to_response(row)


async def run_daily_briefings_for_all_users() -> int:
    """Generate a briefing for every signed-up user who has holdings."""
    db = SessionLocal()
    created = 0
    try:
        users = list_users_with_holdings(db)
        log_event(
            logger,
            logging.INFO,
            "Daily briefing job started",
            user_count=len(users),
        )
        for user in users:
            try:
                await generate_briefing_for_user(db, user)
                created += 1
            except Exception as exc:
                log_event(
                    logger,
                    logging.ERROR,
                    "Daily briefing failed for user",
                    user_id=user.id,
                    error=str(exc),
                )
        log_event(
            logger,
            logging.INFO,
            "Daily briefing job finished",
            created=created,
        )
        return created
    finally:
        db.close()


def run_daily_briefings_sync() -> None:
    """APScheduler entrypoint (sync wrapper around async job)."""
    try:
        asyncio.get_running_loop()
        # Already inside an event loop (unlikely for BackgroundScheduler)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(run_daily_briefings_for_all_users())).result()
    except RuntimeError:
        asyncio.run(run_daily_briefings_for_all_users())

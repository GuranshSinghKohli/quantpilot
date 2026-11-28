"""Session re-exports plus startup data preparation (migrations + guest bootstrap)."""

import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, engine, get_db  # noqa: F401
from app.core.migrations import migration_state, upgrade_to_head
from app.core.settings import BACKEND_DIR, get_settings
from app.db.models import Holding, Portfolio, User
from app.observability.logger import get_logger, log_event

logger = get_logger("db")

LEGACY_WATCHLIST_PATH = BACKEND_DIR / "data" / "watchlist.json"
ANON_EMAIL = "anonymous@local.quantpilot"


def _ensure_anonymous_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == ANON_EMAIL))
    if user is not None:
        if not user.portfolios:
            db.add(Portfolio(user_id=user.id, name="Default"))
            db.commit()
            db.refresh(user)
        return user

    user = User(
        email=ANON_EMAIL,
        hashed_password="!",  # not login-capable
    )
    db.add(user)
    db.flush()
    db.add(Portfolio(user_id=user.id, name="Default"))
    db.commit()
    db.refresh(user)
    log_event(logger, logging.INFO, "Created anonymous local user for unauthenticated watchlist")
    return user


def _migrate_legacy_watchlist(db: Session, anon: User) -> None:
    if not LEGACY_WATCHLIST_PATH.exists():
        return

    portfolio = anon.portfolios[0] if anon.portfolios else None
    if portfolio is None:
        return

    existing = {h.ticker.upper() for h in portfolio.holdings}
    if existing:
        return

    try:
        raw = json.loads(LEGACY_WATCHLIST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_event(
            logger,
            logging.WARNING,
            "Failed to read legacy watchlist.json",
            error=str(exc),
        )
        return

    if not isinstance(raw, list) or not raw:
        return

    imported = 0
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ticker = str(entry.get("ticker", "")).upper().strip()
        if not ticker or ticker in existing:
            continue
        notes = str(entry.get("notes") or "")
        holding = Holding(portfolio_id=portfolio.id, ticker=ticker, notes=notes)
        added_at = entry.get("added_at")
        if isinstance(added_at, str) and added_at:
            try:
                from datetime import datetime

                holding.added_at = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            except ValueError:
                pass
        db.add(holding)
        existing.add(ticker)
        imported += 1

    if imported:
        db.commit()
        log_event(
            logger,
            logging.INFO,
            "Migrated legacy watchlist.json into Postgres/SQLite",
            count=imported,
        )
        try:
            LEGACY_WATCHLIST_PATH.rename(
                LEGACY_WATCHLIST_PATH.with_suffix(".json.migrated")
            )
        except OSError:
            pass


def get_or_create_default_portfolio(db: Session, user: User) -> Portfolio:
    portfolio = db.scalar(
        select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id.asc())
    )
    if portfolio is not None:
        return portfolio
    portfolio = Portfolio(user_id=user.id, name="Default")
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def get_anonymous_user(db: Session) -> User:
    return _ensure_anonymous_user(db)


def _seed() -> None:
    db = SessionLocal()
    try:
        anon = _ensure_anonymous_user(db)
        _migrate_legacy_watchlist(db, anon)
    finally:
        db.close()


def _ensure_sqlite_dir() -> None:
    url = engine.url
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """Migrate to head and seed the guest user. Used by tests and DB_AUTO_MIGRATE."""
    _ensure_sqlite_dir()
    upgrade_to_head(engine)
    _seed()
    log_event(logger, logging.INFO, "Database ready", dialect=engine.dialect.name)


def prepare_database() -> bool:
    """App startup: migrate only when allowed, never touch a schema that is behind."""
    if get_settings().auto_migrate:
        init_db()
        return True
    state = migration_state(engine)
    if not state.is_current:
        log_event(
            logger,
            logging.ERROR,
            "Database schema is not at Alembic head; run `alembic upgrade head`",
            current=state.current,
            head=state.head,
        )
        return False
    _seed()
    return True


def resolve_user_for_watchlist(db: Session, user: Optional[User]) -> User:
    """Authenticated user, or shared anonymous user for guest mode."""
    if user is not None:
        return user
    return get_anonymous_user(db)

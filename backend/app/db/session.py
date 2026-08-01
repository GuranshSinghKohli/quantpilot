import json
import logging
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import (
    BACKEND_DIR,
    DATABASE_URL,
    is_sqlite,
)
from app.db.models import Base, Holding, Portfolio, User
from app.observability.logger import get_logger, log_event

logger = get_logger("db")

_connect_args = {"check_same_thread": False} if is_sqlite() else {}
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

LEGACY_WATCHLIST_PATH = BACKEND_DIR / "data" / "watchlist.json"
ANON_EMAIL = "anonymous@local.quantpilot"


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


def _migrate_holdings_columns() -> None:
    """Add columns introduced after initial table creation (SQLite/Postgres safe)."""
    inspector = inspect(engine)
    if "holdings" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("holdings")}
    additions = {
        "shares": "FLOAT",
        "avg_cost": "FLOAT",
        "source": "VARCHAR(32) DEFAULT 'manual'",
    }
    with engine.begin() as conn:
        for column, ddl_type in additions.items():
            if column in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE holdings ADD COLUMN {column} {ddl_type}"))
                log_event(logger, logging.INFO, "Migrated holdings table", column=column)
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "Holdings column migration skipped",
                    column=column,
                    error=str(exc),
                )


def init_db() -> None:
    if is_sqlite():
        path_part = DATABASE_URL.split("sqlite:///", 1)[-1]
        Path(path_part).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
    _migrate_holdings_columns()
    db = SessionLocal()
    try:
        anon = _ensure_anonymous_user(db)
        _migrate_legacy_watchlist(db, anon)
        log_event(
            logger,
            logging.INFO,
            "Database ready",
            dialect=engine.dialect.name,
            sqlite=is_sqlite(),
        )
    finally:
        db.close()


def resolve_user_for_watchlist(db: Session, user: Optional[User]) -> User:
    """Authenticated user, or shared anonymous user for guest mode."""
    if user is not None:
        return user
    return get_anonymous_user(db)

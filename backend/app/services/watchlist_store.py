"""DB-backed watchlist / holdings store."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Holding, User
from app.db.session import get_or_create_default_portfolio, resolve_user_for_watchlist
from app.models.auth_schemas import WatchlistEntry


def _holding_to_entry(holding: Holding) -> WatchlistEntry:
    added = holding.added_at
    if added is not None and added.tzinfo is None:
        added = added.replace(tzinfo=timezone.utc)
    return WatchlistEntry(
        ticker=holding.ticker,
        added_at=added.isoformat() if added else datetime.now(timezone.utc).isoformat(),
        notes=holding.notes or "",
        shares=holding.shares,
        avg_cost=holding.avg_cost,
        source=holding.source or "manual",
    )


def list_watchlist(db: Session, user: Optional[User] = None) -> List[WatchlistEntry]:
    owner = resolve_user_for_watchlist(db, user)
    portfolio = get_or_create_default_portfolio(db, owner)
    holdings = db.scalars(
        select(Holding)
        .where(Holding.portfolio_id == portfolio.id)
        .order_by(Holding.added_at.desc())
    ).all()
    return [_holding_to_entry(h) for h in holdings]


def list_watchlist_dicts(db: Session, user: Optional[User] = None) -> List[Dict[str, Any]]:
    return [e.model_dump() for e in list_watchlist(db, user)]


def add_holding(
    db: Session,
    ticker: str,
    notes: str = "",
    user: Optional[User] = None,
    shares: Optional[float] = None,
    avg_cost: Optional[float] = None,
    source: str = "manual",
) -> WatchlistEntry:
    symbol = ticker.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker symbol is required.")

    owner = resolve_user_for_watchlist(db, user)
    portfolio = get_or_create_default_portfolio(db, owner)

    existing = db.scalar(
        select(Holding).where(
            Holding.portfolio_id == portfolio.id,
            Holding.ticker == symbol,
        )
    )
    if existing is not None:
        if shares is not None:
            existing.shares = shares
        if avg_cost is not None:
            existing.avg_cost = avg_cost
        if source != "manual":
            existing.source = source
        db.commit()
        db.refresh(existing)
        return _holding_to_entry(existing)

    holding = Holding(
        portfolio_id=portfolio.id,
        ticker=symbol,
        notes=notes or "",
        added_at=datetime.now(timezone.utc),
        shares=shares,
        avg_cost=avg_cost,
        source=source,
    )
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return _holding_to_entry(holding)


def upsert_synced_positions(
    db: Session,
    positions: List[Dict[str, Any]],
    user: Optional[User] = None,
) -> List[WatchlistEntry]:
    """Bulk upsert positions from a confirmed broker sync (Phase 11 extension)."""
    results: List[WatchlistEntry] = []
    for pos in positions:
        ticker = str(pos.get("ticker", "")).upper().strip()
        if not ticker:
            continue
        results.append(
            add_holding(
                db,
                ticker,
                user=user,
                shares=pos.get("shares"),
                avg_cost=pos.get("avg_cost"),
                source="broker_sync",
            )
        )
    return results


def remove_holding(
    db: Session,
    ticker: str,
    user: Optional[User] = None,
) -> Dict[str, str]:
    symbol = ticker.upper().strip()
    owner = resolve_user_for_watchlist(db, user)
    portfolio = get_or_create_default_portfolio(db, owner)

    holding = db.scalar(
        select(Holding).where(
            Holding.portfolio_id == portfolio.id,
            Holding.ticker == symbol,
        )
    )
    if holding is None:
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist.")

    db.delete(holding)
    db.commit()
    return {"status": "removed", "ticker": symbol}

"""Connect one order to the pure fill engine and the repository writer."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.league.config import LeagueSettings
from app.league.models import Order, Position
from app.league.repo.bars import first_bar_after
from app.league.repo.errors import InsufficientCash, InsufficientPosition, OrderNotFound
from app.league.repo.orders import record_fill, set_order_status
from app.league.repo.types import FillInput
from app.league.sim.calendar import MarketCalendar
from app.league.sim.clock import Clock
from app.league.sim.fills import BarQuote, Decision, Expire, FillOrder, Wait, decide_fill

_ACTIVE = frozenset({"accepted", "queued"})


def process_order(
    session: Session,
    order_id: uuid.UUID,
    clock: Clock,
    calendar: MarketCalendar,
    settings: LeagueSettings,
) -> Optional[Decision]:
    """Fill, wait, expire, or reject one order. The caller commits.

    record_fill is the only cash writer. InsufficientCash becomes rejected
    with "insufficient cash incl. commission". InsufficientPosition, and a
    sell when nothing is held, become rejected with "no position".
    CalendarUnavailable is not caught: the order stays where it was.
    """
    order = session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise OrderNotFound(str(order_id))
    if order.status not in _ACTIVE:
        return None

    held = _held_qty(session, order)
    if order.side == "sell" and held <= 0:
        _reject(session, order, "no position")
        return None

    decision = decide_fill(
        FillOrder(side=order.side, notional_usd=order.notional_usd, qty=order.qty),
        order.game_time,
        held,
        settings.SLIPPAGE_BPS,
        _lookup(session, order.ticker),
        clock.now(),
        calendar,
        settings.FILL_TIMEOUT_MINUTES,
    )
    if isinstance(decision, Wait):
        return decision
    if isinstance(decision, Expire):
        set_order_status(session, order.id, "expired", reject_reason=decision.reason)
        return decision
    try:
        record_fill(
            session,
            order.id,
            FillInput(
                price=decision.price,
                qty=decision.qty,
                slippage_bps=Decimal(settings.SLIPPAGE_BPS),
                bar_time=decision.bar_time,
                filled_at=clock.now(),
            ),
            settings.COMMISSION_USD,
        )
    except InsufficientCash:
        session.expire_all()
        _reject(session, order, "insufficient cash incl. commission")
        return None
    except InsufficientPosition:
        session.expire_all()
        _reject(session, order, "no position")
        return None
    return decision


def _held_qty(session: Session, order: Order) -> Decimal:
    position = session.scalar(
        select(Position).where(
            Position.portfolio_id == order.portfolio_id,
            Position.ticker == order.ticker,
        )
    )
    if position is None:
        return Decimal("0")
    return position.qty


def _lookup(session: Session, ticker: str):
    def lookup(after: datetime, until: datetime, *, inclusive: bool) -> Optional[BarQuote]:
        row = first_bar_after(session, ticker, after, until, inclusive=inclusive)
        if row is None:
            return None
        return BarQuote(t=row.t, open=row.o)

    return lookup


def _reject(session: Session, order: Order, reason: str) -> None:
    session.expire(order)
    current = session.scalar(
        select(Order)
        .where(Order.id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise OrderNotFound(str(order.id))
    if current.status not in _ACTIVE:
        return
    set_order_status(session, current.id, "rejected", reject_reason=reason)

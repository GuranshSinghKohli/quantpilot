"""Orders and the one writer that marks an order filled."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.league.models import Fill, Order, Portfolio
from app.league.repo.errors import (
    FillBeforeOrder,
    FillNotPositive,
    InvalidCommission,
    OrderNotFillable,
    OrderNotFound,
    PortfolioNotFound,
    StatusNotAllowed,
    StatusTransitionNotAllowed,
)
from app.league.repo.portfolios import _apply_fill, quantize_fill
from app.league.repo.types import FillInput

# TODO(week 3): approve mode may create and set status 'pending_approval'.
_WRITABLE_STATUSES = frozenset({"accepted", "rejected", "queued", "expired", "cancelled"})
_FILLABLE_STATUSES = frozenset({"accepted", "queued"})
_TERMINAL_STATUSES = frozenset({"filled", "rejected", "expired", "cancelled"})
_TRANSITIONS = {
    "accepted": frozenset({"queued", "rejected", "expired", "cancelled"}),
    "queued": frozenset({"accepted", "rejected", "expired", "cancelled"}),
}


def _require_writable_status(status: str) -> None:
    if status not in _WRITABLE_STATUSES:
        raise StatusNotAllowed(status)


def create_order(
    session: Session,
    *,
    portfolio_id: uuid.UUID,
    ticker: str,
    side: str,
    source: str,
    status: str,
    game_time: datetime,
    notional_usd: Optional[Decimal] = None,
    qty: Optional[Decimal] = None,
    rationale_id: Optional[uuid.UUID] = None,
    cycle_id: Optional[uuid.UUID] = None,
    reject_reason: Optional[str] = None,
) -> Order:
    _require_writable_status(status)
    order = Order(
        portfolio_id=portfolio_id,
        ticker=ticker,
        side=side,
        source=source,
        status=status,
        game_time=game_time,
        notional_usd=notional_usd,
        qty=qty,
        rationale_id=rationale_id,
        cycle_id=cycle_id,
        reject_reason=reject_reason,
    )
    session.add(order)
    session.flush()
    return order


def set_order_status(
    session: Session,
    order_id: uuid.UUID,
    status: str,
    reject_reason: Optional[str] = None,
) -> Order:
    _require_writable_status(status)
    order = session.get(Order, order_id)
    if order is None:
        raise OrderNotFound(str(order_id))
    if order.status in _TERMINAL_STATUSES or status not in _TRANSITIONS.get(order.status, ()):
        raise StatusTransitionNotAllowed(f"{order.status} -> {status}")
    order.status = status
    if reject_reason is not None:
        order.reject_reason = reject_reason
    session.flush()
    return order


def record_fill(
    session: Session,
    order_id: uuid.UUID,
    fill: FillInput,
    commission: Decimal,
) -> Fill:
    """Insert the fill, mark the order filled, and update cash and the position.

    The savepoint rolls back every write in this function when anything raises.
    The caller owns the transaction and is the one that commits.
    """
    if commission < 0:
        raise InvalidCommission(str(commission))
    if fill.price <= 0 or fill.qty <= 0:
        raise FillNotPositive(f"price {fill.price} qty {fill.qty}")

    with session.begin_nested():
        order = session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None:
            raise OrderNotFound(str(order_id))
        if fill.bar_time <= order.game_time:
            raise FillBeforeOrder(
                f"bar {fill.bar_time.isoformat()} order {order.game_time.isoformat()}"
            )
        if order.status not in _FILLABLE_STATUSES:
            raise OrderNotFillable(order.status)

        portfolio = session.scalar(
            select(Portfolio)
            .where(Portfolio.id == order.portfolio_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if portfolio is None:
            raise PortfolioNotFound(str(order.portfolio_id))

        qty, price = quantize_fill(order.side, fill.qty, fill.price)
        if qty <= 0 or price <= 0:
            raise FillNotPositive(f"price {price} qty {qty}")

        fill_row = Fill(
            order_id=order.id,
            price=price,
            qty=qty,
            slippage_bps=fill.slippage_bps,
            bar_time=fill.bar_time,
            filled_at=fill.filled_at,
        )
        session.add(fill_row)
        order.status = "filled"
        session.flush()
        _apply_fill(
            session,
            portfolio,
            order.ticker,
            order.side,
            qty,
            price,
            commission,
            fill.filled_at,
        )
        return fill_row

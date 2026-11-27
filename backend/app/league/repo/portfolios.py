"""Portfolios and positions.

_apply_fill is private. record_fill is the only public writer of cash and positions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.league.models import Portfolio, Position
from app.league.repo.errors import (
    InsufficientCash,
    InsufficientPosition,
    LeagueRepoError,
    PortfolioNotFound,
)
from app.league.repo.types import PortfolioView

_CASH_QUANTUM = Decimal("0.0001")
_QTY_QUANTUM = Decimal("0.000001")
_BASIS_QUANTUM = Decimal("0.0000000001")


def _quantize_debit(amount: Decimal) -> Decimal:
    """Round a cash amount taken from the player up, so rounding never creates money."""
    return amount.quantize(_CASH_QUANTUM, rounding=ROUND_CEILING)


def _quantize_credit(amount: Decimal) -> Decimal:
    """Round a cash amount given to the player down."""
    return amount.quantize(_CASH_QUANTUM, rounding=ROUND_FLOOR)


def quantize_fill(side: str, qty: Decimal, price: Decimal) -> tuple[Decimal, Decimal]:
    """House rounding. Buys pay a higher price. Neither side receives extra shares."""
    qty_q = qty.quantize(_QTY_QUANTUM, rounding=ROUND_DOWN)
    price_rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    price_q = price.quantize(_CASH_QUANTUM, rounding=price_rounding)
    return qty_q, price_q


def _average_cost(cost_basis: Decimal, qty: Decimal) -> Decimal:
    return (cost_basis / qty).quantize(_CASH_QUANTUM, rounding=ROUND_HALF_EVEN)


def _remaining_basis(cost_basis: Decimal, old_qty: Decimal, new_qty: Decimal) -> Decimal:
    return (cost_basis * new_qty / old_qty).quantize(_BASIS_QUANTUM, rounding=ROUND_HALF_EVEN)


def create_portfolio(
    session: Session,
    agent_id: uuid.UUID,
    season_id: uuid.UUID,
    kind: str,
    cash: Decimal,
) -> Portfolio:
    portfolio = Portfolio(agent_id=agent_id, season_id=season_id, kind=kind, cash=cash)
    session.add(portfolio)
    session.flush()
    return portfolio


def get_portfolio(session: Session, portfolio_id: uuid.UUID) -> PortfolioView:
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise PortfolioNotFound(str(portfolio_id))
    positions = session.scalars(
        select(Position)
        .where(Position.portfolio_id == portfolio_id)
        .order_by(Position.ticker.asc())
    ).all()
    return PortfolioView(portfolio=portfolio, positions=tuple(positions))


def _apply_fill(
    session: Session,
    portfolio: Portfolio,
    ticker: str,
    side: str,
    qty: Decimal,
    price: Decimal,
    commission: Decimal,
    at: datetime,
) -> None:
    """Apply section 7.4 cash formulas and the position change.

    The caller has already locked the portfolio. This locks the position row
    after that, never before.
    """
    if side not in ("buy", "sell"):
        raise LeagueRepoError(f"side {side}")
    position = session.scalar(
        select(Position)
        .where(Position.portfolio_id == portfolio.id, Position.ticker == ticker)
        .with_for_update()
    )
    if side == "sell" and (position is None or position.qty < qty):
        raise InsufficientPosition(f"{ticker} held {position.qty if position else 0}, sell {qty}")

    if side == "buy":
        delta = -_quantize_debit(qty * price + commission)
    else:
        proceeds = qty * price - commission
        if proceeds >= 0:
            delta = _quantize_credit(proceeds)
        else:
            delta = -_quantize_debit(-proceeds)

    if portfolio.cash + delta < 0:
        raise InsufficientCash(f"cash {portfolio.cash} delta {delta}")

    portfolio.cash = portfolio.cash + delta
    if side == "buy":
        traded = qty * price
        if position is None:
            session.add(
                Position(
                    portfolio_id=portfolio.id,
                    ticker=ticker,
                    qty=qty,
                    cost_basis=traded,
                    avg_cost=_average_cost(traded, qty),
                    updated_at=at,
                )
            )
        else:
            new_qty = position.qty + qty
            basis = position.cost_basis + traded
            position.cost_basis = basis
            position.qty = new_qty
            position.avg_cost = _average_cost(basis, new_qty)
            position.updated_at = at
    else:
        remaining = position.qty - qty
        if remaining == 0:
            session.delete(position)
        else:
            basis = _remaining_basis(position.cost_basis, position.qty, remaining)
            position.cost_basis = basis
            position.qty = remaining
            position.avg_cost = _average_cost(basis, remaining)
            position.updated_at = at
    session.flush()

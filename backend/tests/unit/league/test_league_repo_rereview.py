"""Second-pass probes for cost basis, house rounding, and terminal statuses."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.league.models import Fill, Order, Position
from app.league.repo import (
    FillNotPositive,
    StatusNotAllowed,
    StatusTransitionNotAllowed,
    get_portfolio,
    record_fill,
    set_order_status,
)
from tests.unit.league.test_league_migration import _config, _engine, _url
from tests.unit.league.test_league_repo import _fill, _order, _seed, _user_count

_CASH = Decimal("0.0001")
_QTY = Decimal("0.000001")
_BASIS = Decimal("0.0000000001")


@pytest.fixture
def league_session():
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )()
    try:
        seed = _seed(session)
        yield session, seed, engine
    finally:
        session.close()
        outer.rollback()
        connection.close()
        try:
            after = _user_count(engine)
        finally:
            engine.dispose()
        assert after == before


def _position_rows(session, portfolio_id) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Position)
            .where(Position.portfolio_id == portfolio_id)
        )
    )


def test_house_rounding_on_buy_and_sell_is_what_gets_stored(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id, notional_usd=Decimal("2"))
    record_fill(
        session,
        buy.id,
        _fill(price=Decimal("1.00001"), qty=Decimal("1.0000009")),
        Decimal("0"),
    )
    session.expire_all()
    buy_fill = session.scalar(select(Fill).where(Fill.order_id == buy.id))
    assert buy_fill is not None
    assert buy_fill.qty == Decimal("1.000000")
    assert buy_fill.price == Decimal("1.0001")
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions[0].qty == Decimal("1.000000")
    assert view.positions[0].cost_basis == Decimal("1.0001000000")
    assert view.portfolio.cash == Decimal("998.9999")

    sell_qty = Decimal("0.4000009")
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(
        session,
        sell.id,
        _fill(price=Decimal("1.00009"), qty=sell_qty),
        Decimal("0"),
    )
    session.expire_all()
    sell_fill = session.scalar(select(Fill).where(Fill.order_id == sell.id))
    assert sell_fill is not None
    assert sell_fill.qty == Decimal("0.400000")
    assert sell_fill.price == Decimal("1.0000")
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions[0].qty == Decimal("0.600000")
    assert view.portfolio.cash == Decimal("999.3999")
    assert view.positions[0].cost_basis >= 0
    assert view.positions[0].avg_cost == (
        view.positions[0].cost_basis / view.positions[0].qty
    ).quantize(_CASH, rounding=ROUND_HALF_EVEN)


def test_buy_below_one_hundredth_of_a_cent_rounds_the_price_up(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    record_fill(session, order.id, _fill(price=Decimal("0.00001"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    stored = session.scalar(select(Fill).where(Fill.order_id == order.id))
    assert stored is not None
    assert stored.price == Decimal("0.0001")
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("999.9999")


def test_sell_request_that_rounds_down_to_the_holding_closes_it(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id)
    record_fill(session, buy.id, _fill(price=Decimal("10"), qty=Decimal("1")), Decimal("0"))
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(
        session, sell.id, _fill(price=Decimal("10"), qty=Decimal("1.0000004")), Decimal("0")
    )
    session.expire_all()
    assert get_portfolio(session, seed.portfolio_id).positions == ()
    assert _position_rows(session, seed.portfolio_id) == 0


def test_partial_sell_cycles_then_full_exit_leaves_no_cost_basis(league_session) -> None:
    session, seed, _engine = league_session
    buy_prices = (
        Decimal("10.00015"),
        Decimal("3.33333"),
        Decimal("8.00001"),
        Decimal("12.50009"),
        Decimal("1.00001"),
        Decimal("6.25006"),
    )
    for index, price in enumerate(buy_prices):
        order = _order(
            session,
            seed.portfolio_id,
            ticker="AAPL",
            notional_usd=Decimal("20"),
        )
        record_fill(
            session,
            order.id,
            _fill(price=price, qty=Decimal(f"1.000000{index + 1}")),
            Decimal("0.0001"),
        )
        session.expire_all()
        held = get_portfolio(session, seed.portfolio_id).positions[0]
        held_qty = Decimal(held.qty)
        held_basis = Decimal(held.cost_basis)
        assert held_basis >= 0
        assert held_qty > 0
        assert held.avg_cost == (held_basis / held_qty).quantize(
            _CASH, rounding=ROUND_HALF_EVEN
        )
        part = (held_qty / 2).quantize(_QTY, rounding=ROUND_DOWN)
        assert part > 0
        sell = _order(
            session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1")
        )
        record_fill(
            session,
            sell.id,
            _fill(price=price - Decimal("0.00004"), qty=part),
            Decimal("0"),
        )
        session.expire_all()
        left = get_portfolio(session, seed.portfolio_id).positions
        assert len(left) == 1
        assert left[0].qty == held_qty - part
        expected_basis = (held_basis * left[0].qty / held_qty).quantize(
            _BASIS, rounding=ROUND_HALF_EVEN
        )
        assert left[0].cost_basis == expected_basis
        assert left[0].avg_cost == (left[0].cost_basis / left[0].qty).quantize(
            _CASH, rounding=ROUND_HALF_EVEN
        )

    session.expire_all()
    remaining = get_portfolio(session, seed.portfolio_id).positions[0].qty
    close = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(session, close.id, _fill(price=Decimal("4"), qty=remaining), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions == ()
    assert _position_rows(session, seed.portfolio_id) == 0
    leftover = session.scalar(
        select(func.coalesce(func.sum(Position.cost_basis), 0)).where(
            Position.portfolio_id == seed.portfolio_id
        )
    )
    assert leftover == 0


def test_illegal_transitions_around_the_terminal_set(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _order(session, seed.portfolio_id, ticker="AAA")
    with pytest.raises(StatusTransitionNotAllowed):
        set_order_status(session, accepted.id, "accepted")
    queued = _order(session, seed.portfolio_id, ticker="QQQ", status="queued")
    set_order_status(session, queued.id, "rejected")
    still_queued = _order(session, seed.portfolio_id, ticker="QQ2", status="queued")
    with pytest.raises(StatusTransitionNotAllowed):
        set_order_status(session, still_queued.id, "queued")
    set_order_status(session, still_queued.id, "cancelled")
    with pytest.raises(StatusTransitionNotAllowed):
        set_order_status(session, queued.id, "accepted")
    with pytest.raises(StatusNotAllowed):
        set_order_status(session, accepted.id, "filled")
    dust = _order(session, seed.portfolio_id, ticker="DUST", side="sell", notional_usd=None, qty=Decimal("1"))
    with pytest.raises(FillNotPositive):
        record_fill(
            session, dust.id, _fill(price=Decimal("0.00001"), qty=Decimal("1")), Decimal("0")
        )
    session.expire_all()
    assert session.get(Order, accepted.id).status == "accepted"
    assert session.get(Order, queued.id).status == "rejected"
    assert session.get(Order, still_queued.id).status == "cancelled"
    assert session.get(Order, dust.id).status == "accepted"
    assert session.scalar(select(func.count()).select_from(Fill).where(Fill.order_id == dust.id)) == 0

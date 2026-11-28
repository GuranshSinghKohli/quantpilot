"""Reviewer probes for record_fill. These assert the spec, not the current code."""

from __future__ import annotations

import threading
import uuid
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.league.models import Order, Portfolio
from app.league.repo import (
    FillNotPositive,
    InsufficientCash,
    InsufficientPosition,
    InvalidCommission,
    OrderNotFillable,
    StatusNotAllowed,
    StatusTransitionNotAllowed,
    create_portfolio,
    get_portfolio,
    record_fill,
    set_order_status,
)
from tests.unit.league.test_league_migration import _config, _engine, _url
from tests.unit.league.test_league_repo import (
    _fill,
    _order,
    _seed,
    _user_count,
    _wipe_user,
)

_CASH = Decimal("0.0001")


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


def _cash(session, portfolio_id) -> Decimal:
    session.expire_all()
    return get_portfolio(session, portfolio_id).portfolio.cash


def test_midpoint_debit_rounds_up_and_midpoint_credit_rounds_down(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id, notional_usd=Decimal("2"))
    record_fill(session, buy.id, _fill(price=Decimal("1.00005"), qty=Decimal("1")), Decimal("0"))
    assert _cash(session, seed.portfolio_id) == Decimal("998.9999")
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(session, sell.id, _fill(price=Decimal("1.00005"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("999.9999")
    assert view.positions == ()


def test_sub_cent_credit_floors_to_zero(league_session) -> None:
    """A representable price whose product is below 0.0001 credits nothing."""
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id)
    record_fill(session, buy.id, _fill(price=Decimal("10"), qty=Decimal("1")), Decimal("0"))
    sell_qty = Decimal("0.000001")
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=sell_qty)
    record_fill(session, sell.id, _fill(price=Decimal("0.0001"), qty=sell_qty), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("990")
    assert view.positions[0].qty == Decimal("0.999999")


def test_positive_price_that_rounds_to_zero_does_not_fill(league_session) -> None:
    """A sell floors 0.00001 to 0.0000, so the fill is refused before a write."""
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    with pytest.raises(FillNotPositive):
        record_fill(session, order.id, _fill(price=Decimal("0.00001"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("1000")
    assert view.positions == ()


def test_exact_sell_with_commission_above_proceeds_debits_and_deletes(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id)
    record_fill(session, buy.id, _fill(price=Decimal("10"), qty=Decimal("1")), Decimal("0"))
    held = get_portfolio(session, seed.portfolio_id).positions[0].qty
    price = Decimal("1.00005")
    commission = Decimal("2")
    shortfall = -(held * price - commission)
    debit = shortfall.quantize(_CASH, rounding=ROUND_CEILING)
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=held)
    record_fill(session, sell.id, _fill(price=price, qty=held), commission)
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions == ()
    assert view.portfolio.cash == Decimal("990") - debit


def test_exact_sell_with_unpayable_commission_leaves_the_position(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id, notional_usd=Decimal("1000"))
    record_fill(session, buy.id, _fill(price=Decimal("10"), qty=Decimal("100")), Decimal("0"))
    assert _cash(session, seed.portfolio_id) == Decimal("0")
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("100"))
    with pytest.raises(InsufficientCash):
        record_fill(
            session,
            sell.id,
            _fill(price=Decimal("0.01"), qty=Decimal("100")),
            Decimal("10"),
        )
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("0")
    assert len(view.positions) == 1
    assert view.positions[0].qty == Decimal("100")
    assert session.get(Order, sell.id).status == "accepted"


def test_avg_cost_after_many_buys_is_one_half_even_of_the_true_average(league_session) -> None:
    """Storage rounding happens once. Repeating it must not move the average."""
    session, seed, _engine = league_session
    lots = (
        (Decimal("1"), Decimal("1.0000")),
        (Decimal("1"), Decimal("1.0003")),
        (Decimal("1"), Decimal("0.9998")),
        (Decimal("0.000001"), Decimal("3.33335")),
        (Decimal("0.000001"), Decimal("3.33335")),
        (Decimal("0.000001"), Decimal("3.33335")),
    )
    total_qty = sum((qty for qty, _price in lots), Decimal("0"))
    total_cost = sum((qty * price for qty, price in lots), Decimal("0"))
    expected = (total_cost / total_qty).quantize(_CASH, rounding=ROUND_HALF_EVEN)
    for qty, price in lots:
        order = _order(
            session,
            seed.portfolio_id,
            notional_usd=None,
            qty=qty,
        )
        record_fill(session, order.id, _fill(price=price, qty=qty), Decimal("0"))
    session.expire_all()
    position = get_portfolio(session, seed.portfolio_id).positions[0]
    assert position.qty == total_qty
    assert position.avg_cost == expected


def test_fill_on_one_portfolio_does_not_touch_another(league_session) -> None:
    session, seed, _engine = league_session
    actual = session.get(Portfolio, seed.portfolio_id)
    assert actual is not None
    ghost = create_portfolio(
        session, actual.agent_id, actual.season_id, "ghost", Decimal("500")
    )
    order = _order(session, ghost.id, ticker="MSFT")
    record_fill(session, order.id, _fill(price=Decimal("10"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    untouched = get_portfolio(session, seed.portfolio_id)
    touched = get_portfolio(session, ghost.id)
    assert untouched.portfolio.cash == Decimal("1000")
    assert untouched.positions == ()
    assert touched.portfolio.cash == Decimal("490")
    assert len(touched.positions) == 1
    assert touched.positions[0].ticker == "MSFT"
    assert touched.positions[0].qty == Decimal("1")


def test_qty_at_numeric_18_6_is_stored_and_does_not_grant_extra_shares(league_session) -> None:
    session, seed, _engine = league_session
    exact = _order(session, seed.portfolio_id, notional_usd=None, qty=Decimal("1.123456"))
    record_fill(
        session, exact.id, _fill(price=Decimal("1"), qty=Decimal("1.123456")), Decimal("0")
    )
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions[0].qty == Decimal("1.123456")
    assert view.portfolio.cash == Decimal("998.8765")

    rounded = _order(session, seed.portfolio_id, ticker="MSFT", notional_usd=Decimal("400"))
    price = Decimal("300")
    qty = Decimal("1.0000005")
    record_fill(session, rounded.id, _fill(price=price, qty=qty), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    stored = next(position for position in view.positions if position.ticker == "MSFT")
    debit = Decimal("998.8765") - view.portfolio.cash
    fair = (stored.qty * price).quantize(_CASH, rounding=ROUND_CEILING)
    assert fair <= debit


def test_qty_finer_than_six_decimals_does_not_stick(league_session) -> None:
    session, seed, _engine = league_session
    dust = _order(session, seed.portfolio_id, ticker="NVDA", notional_usd=Decimal("1"))
    with pytest.raises(Exception):
        record_fill(
            session, dust.id, _fill(price=Decimal("10"), qty=Decimal("0.0000004")), Decimal("0")
        )
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions == ()
    assert view.portfolio.cash == Decimal("1000")
    assert session.get(Order, dust.id).status == "accepted"


def test_rejected_expired_and_cancelled_orders_do_not_fill(league_session) -> None:
    session, seed, _engine = league_session
    for status in ("rejected", "expired", "cancelled"):
        order = _order(session, seed.portfolio_id, ticker=status[:4].upper())
        set_order_status(session, order.id, status)
        with pytest.raises(OrderNotFillable):
            record_fill(session, order.id, _fill(), Decimal("0"))
        session.expire_all()
        assert session.get(Order, order.id).status == status
    assert _cash(session, seed.portfolio_id) == Decimal("1000")


def test_two_orders_on_one_portfolio_both_fill() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                seed = _seed(session)
                user_id = seed.user_id
                first = _order(session, seed.portfolio_id, ticker="AAPL")
                second = _order(session, seed.portfolio_id, ticker="MSFT")
                portfolio_id = seed.portfolio_id
                order_ids = (first.id, second.id)
        outcomes: list[str] = []

        from sqlalchemy import text

        def _attempt_clean(order_id: uuid.UUID) -> None:
            with factory() as session:
                try:
                    with session.begin():
                        session.execute(text("SET LOCAL lock_timeout = '5s'"))
                        record_fill(session, order_id, _fill(), Decimal("0"))
                    outcomes.append("ok")
                except Exception as exc:  # pragma: no cover - failure detail
                    outcomes.append(f"error:{type(exc).__name__}:{exc}")

        threads = [
            threading.Thread(target=_attempt_clean, args=(order_ids[0],)),
            threading.Thread(target=_attempt_clean, args=(order_ids[1],)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["ok", "ok"]
        with factory() as session:
            view = get_portfolio(session, portfolio_id)
            assert view.portfolio.cash == Decimal("980")
            assert sorted(position.ticker for position in view.positions) == ["AAPL", "MSFT"]
            assert all(position.qty == Decimal("1") for position in view.positions)
    finally:
        if user_id is not None:
            _wipe_user(engine, user_id)
        try:
            after = _user_count(engine)
        finally:
            engine.dispose()
        assert after == before


def test_negative_cost_basis_is_rejected(league_session) -> None:
    session, seed, _engine = league_session
    with pytest.raises(IntegrityError) as caught:
        with session.begin_nested():
            session.execute(
                text(
                    """
                    INSERT INTO league.positions
                        (portfolio_id, ticker, qty, avg_cost, cost_basis)
                    VALUES (:portfolio, 'IBM', 1, 1, -0.0000000001)
                    """
                ),
                {"portfolio": seed.portfolio_id},
            )
    assert "ck_positions_cost_basis_nonnegative" in str(caught.value)


def test_negative_commission_is_rejected_before_a_write(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    with pytest.raises(InvalidCommission):
        record_fill(session, order.id, _fill(), Decimal("-0.01"))
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("1000")
    assert view.positions == ()


def test_terminal_statuses_refuse_every_change(league_session) -> None:
    session, seed, _engine = league_session
    filled = _order(session, seed.portfolio_id, ticker="AAPL")
    record_fill(session, filled.id, _fill(), Decimal("0"))
    terminals = {"filled": filled.id}
    for status in ("rejected", "expired", "cancelled"):
        order = _order(session, seed.portfolio_id, ticker=status[:4].upper())
        set_order_status(session, order.id, status)
        terminals[status] = order.id
    targets = (
        "accepted",
        "queued",
        "rejected",
        "expired",
        "cancelled",
        "filled",
        "pending_approval",
    )
    for current, order_id in terminals.items():
        for target in targets:
            expected = (
                StatusNotAllowed
                if target in ("filled", "pending_approval")
                else StatusTransitionNotAllowed
            )
            with pytest.raises(expected):
                set_order_status(session, order_id, target)
            session.expire_all()
            assert session.get(Order, order_id).status == current


def test_allowed_status_transitions(league_session) -> None:
    session, seed, _engine = league_session
    for target in ("queued", "rejected", "expired", "cancelled"):
        order = _order(session, seed.portfolio_id, ticker=f"A{target[:3].upper()}")
        set_order_status(session, order.id, target)
        session.expire_all()
        assert session.get(Order, order.id).status == target
    for target in ("accepted", "rejected", "expired", "cancelled"):
        order = _order(
            session,
            seed.portfolio_id,
            ticker=f"Q{target[:3].upper()}",
            status="queued",
        )
        set_order_status(session, order.id, target)
        session.expire_all()
        assert session.get(Order, order.id).status == target


_QTY_Q = Decimal("0.000001")
_CASH_Q = Decimal("0.0001")
_BASIS_Q = Decimal("0.0000000001")


def _house_qty(qty: Decimal) -> Decimal:
    return qty.quantize(_QTY_Q, rounding=ROUND_DOWN)


def _house_price(side: str, price: Decimal) -> Decimal:
    rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    return price.quantize(_CASH_Q, rounding=rounding)


@settings(
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(["AAPL", "MSFT", "NVDA"]),
            st.sampled_from(["buy", "sell"]),
            st.decimals(min_value=Decimal("0.0000001"), max_value=Decimal("4"), places=7),
            st.decimals(min_value=Decimal("0.00001"), max_value=Decimal("40"), places=5),
            st.decimals(min_value=Decimal("0"), max_value=Decimal("0.5"), places=4),
        ),
        min_size=1,
        max_size=8,
    )
)
def test_record_fill_preserves_cash_qty_and_average(league_session, steps) -> None:
    session, seed, _engine = league_session
    nested = session.begin_nested()
    try:
        cash = Decimal("1000")
        debits = Decimal("0")
        credits = Decimal("0")
        book: dict[str, tuple[Decimal, Decimal]] = {}
        for ticker, side, qty, price, commission in steps:
            qty_q = _house_qty(qty)
            price_q = _house_price(side, price)
            held, basis = book.get(ticker, (Decimal("0"), Decimal("0")))
            order = _order(
                session,
                seed.portfolio_id,
                ticker=ticker,
                side=side,
                notional_usd=None if side == "sell" else Decimal("10"),
                qty=Decimal("1") if side == "sell" else None,
            )
            rejected = qty_q <= 0 or price_q <= 0 or (side == "sell" and held < qty_q)
            if side == "buy":
                debit = (qty_q * price_q + commission).quantize(_CASH_Q, rounding=ROUND_CEILING)
                credit = Decimal("0")
            else:
                proceeds = qty_q * price_q - commission
                if proceeds >= 0:
                    credit = proceeds.quantize(_CASH_Q, rounding=ROUND_FLOOR)
                    debit = Decimal("0")
                else:
                    debit = (-proceeds).quantize(_CASH_Q, rounding=ROUND_CEILING)
                    credit = Decimal("0")
            if not rejected and cash - debit + credit < 0:
                rejected = True
                with pytest.raises(InsufficientCash):
                    record_fill(session, order.id, _fill(price=price, qty=qty), commission)
            elif rejected:
                with pytest.raises((FillNotPositive, InsufficientPosition)):
                    record_fill(session, order.id, _fill(price=price, qty=qty), commission)
            else:
                record_fill(session, order.id, _fill(price=price, qty=qty), commission)
                debits += debit
                credits += credit
                cash = cash - debit + credit
                if side == "buy":
                    book[ticker] = (held + qty_q, basis + qty_q * price_q)
                else:
                    new_qty = held - qty_q
                    if new_qty == 0:
                        book.pop(ticker, None)
                    else:
                        book[ticker] = (
                            new_qty,
                            (basis * new_qty / held).quantize(_BASIS_Q, rounding=ROUND_HALF_EVEN),
                        )
            session.expire_all()
            view = get_portfolio(session, seed.portfolio_id)
            assert view.portfolio.cash == cash
            assert view.portfolio.cash == Decimal("1000") - debits + credits
            assert view.portfolio.cash >= 0
            assert {position.ticker for position in view.positions} == set(book)
            for position in view.positions:
                expect_qty, expect_basis = book[position.ticker]
                assert position.qty > 0
                assert position.qty == expect_qty
                assert position.cost_basis == expect_basis
                assert position.avg_cost == (expect_basis / expect_qty).quantize(
                    _CASH_Q, rounding=ROUND_HALF_EVEN
                )
    finally:
        nested.rollback()

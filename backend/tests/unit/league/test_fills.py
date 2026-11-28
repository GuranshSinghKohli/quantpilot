"""Fill engine: session bars, slippage, timeout, and process_order."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.league.config import LeagueSettings
from app.league.models import Fill as FillRow
from app.league.models import Order, Position
from app.league.repo import BarInput, OrderNotFillable, create_order, get_portfolio, upsert_bars
from app.league.repo.bars import first_bar_after
from app.league.repo.portfolios import quantize_fill
from app.league.sim.calendar import CalendarUnavailable, SessionCalendar
from app.league.sim.clock import ManualClock
from app.league.sim.fills import BarQuote, Expire, Fill, FillOrder, Wait, decide_fill
from app.league.sim.process import process_order
from tests.unit.league.test_league_migration import _config, _engine, _url
from tests.unit.league.test_league_repo import _order, _seed, _user_count, _wipe_user

_UTC = timezone.utc
_ET = ZoneInfo("America/New_York")
_OPEN = Decimal("10.0001")


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_ET).astimezone(_UTC)


def _utc(hour: int, minute: int, *, day: int = 2) -> datetime:
    return datetime(2026, 1, day, hour, minute, tzinfo=_UTC)


def _bar(when: datetime, price: str = "10", ticker: str = "AAPL") -> BarInput:
    value = Decimal(price)
    return BarInput(ticker=ticker, t=when, o=value, h=value, l=value, c=value, v=1, feed="iex")


def _quote(when: datetime, price: Decimal = Decimal("10")) -> BarQuote:
    return BarQuote(t=when, open=price)


def _settings(**overrides: object) -> LeagueSettings:
    values = {
        "SLIPPAGE_BPS": 0,
        "FILL_TIMEOUT_MINUTES": 30,
        "COMMISSION_USD": Decimal("0"),
    }
    values.update(overrides)
    return LeagueSettings(**values)


def _regular_days() -> SessionCalendar:
    return SessionCalendar(
        (
            (_et(2026, 1, 2, 9, 30), _et(2026, 1, 2, 16, 0)),
            (_et(2026, 1, 5, 9, 30), _et(2026, 1, 5, 16, 0)),
        )
    )


def _early_close() -> SessionCalendar:
    return SessionCalendar(
        (
            (_et(2026, 1, 2, 9, 30), _et(2026, 1, 2, 13, 0)),
            (_et(2026, 1, 5, 9, 30), _et(2026, 1, 5, 16, 0)),
        )
    )


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


def _count(session, model, portfolio_id: uuid.UUID | None = None) -> int:
    query = select(func.count()).select_from(model)
    if portfolio_id is not None and model is Position:
        query = query.where(Position.portfolio_id == portfolio_id)
    if portfolio_id is not None and model is FillRow:
        query = query.join(Order, Order.id == FillRow.order_id).where(Order.portfolio_id == portfolio_id)
    return int(session.scalar(query) or 0)


def _fills_for(session, order_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(FillRow).where(FillRow.order_id == order_id)
        )
        or 0
    )


def test_bar_at_t_is_skipped_and_gaps_are_not_filled(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    later = _utc(15, 5)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(accepted, "10"), _bar(later, "11")])
    decision = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("11")),
        accepted,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        _utc(15, 10),
        calendar,
        30,
    )
    assert isinstance(decision, Fill)
    assert decision.bar_time == later
    assert decision.price == Decimal("11")


def test_a_bar_after_now_is_invisible(league_session) -> None:
    session, _seed, _engine = league_session
    accepted = _utc(15, 0)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(_utc(15, 20))])
    decision = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("10")),
        accepted,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        _utc(15, 10),
        calendar,
        30,
    )
    assert isinstance(decision, Wait)


def test_inclusive_lower_bound_is_opt_in(league_session) -> None:
    session, _seed, _engine = league_session
    stamp = _utc(15, 0)
    upsert_bars(session, [_bar(stamp)])
    assert first_bar_after(session, "AAPL", stamp, _utc(15, 5)) is None
    found = first_bar_after(session, "AAPL", stamp, _utc(15, 5), inclusive=True)
    assert found is not None
    assert found.t == stamp


def test_open_order_does_not_fill_a_post_close_bar(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _et(2026, 1, 2, 15, 59)
    post_close = _et(2026, 1, 2, 16, 1)
    next_open = _et(2026, 1, 5, 9, 30)
    calendar = _regular_days()
    upsert_bars(session, [_bar(post_close, "9"), _bar(next_open, "12")])
    waiting = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("12")),
        accepted,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        _et(2026, 1, 2, 16, 5),
        calendar,
        30,
    )
    assert isinstance(waiting, Wait)
    filled = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("12")),
        accepted,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        next_open,
        calendar,
        30,
    )
    assert isinstance(filled, Fill)
    assert filled.bar_time == next_open
    assert calendar.is_open(filled.bar_time)
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("12"))
    assert isinstance(
        process_order(session, order.id, ManualClock(next_open), calendar, _settings()),
        Fill,
    )
    assert _fills_for(session, order.id) == 1


def test_closed_order_fills_at_the_next_open_not_after_hours(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _et(2026, 1, 2, 17, 0)
    after_hours = _et(2026, 1, 2, 17, 30)
    next_open = _et(2026, 1, 5, 9, 30)
    calendar = _regular_days()
    upsert_bars(session, [_bar(after_hours, "8"), _bar(next_open, "13")])
    decision = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        next_open,
        calendar,
        30,
    )
    assert isinstance(decision, Fill)
    assert decision.bar_time == next_open
    order = _order(
        session,
        seed.portfolio_id,
        game_time=accepted,
        status="queued",
        notional_usd=None,
        qty=Decimal("1"),
    )
    process_order(session, order.id, ManualClock(next_open), calendar, _settings())
    session.expire_all()
    assert session.get(Order, order.id).status == "filled"
    assert _fills_for(session, order.id) == 1


def test_slippage_rounds_buys_up_and_sells_down() -> None:
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    bars = [_quote(bar_time, _OPEN)]

    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        for bar in bars:
            if bar.t > until:
                return None
            if inclusive and bar.t >= after:
                return bar
            if not inclusive and bar.t > after:
                return bar
        return None

    buy = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("20")),
        accepted,
        Decimal("0"),
        1,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    sell = decide_fill(
        FillOrder(side="sell", qty=Decimal("1")),
        accepted,
        Decimal("1"),
        1,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    assert isinstance(buy, Fill)
    assert isinstance(sell, Fill)
    _ignored, buy_price = quantize_fill("buy", Decimal("1"), _OPEN * Decimal("1.0001"))
    _ignored, sell_price = quantize_fill("sell", Decimal("1"), _OPEN * Decimal("0.9999"))
    assert buy.price == buy_price
    assert sell.price == sell_price
    assert buy.price > _OPEN
    assert sell.price < _OPEN
    assert buy.qty * buy.price <= Decimal("20")


def test_notional_buy_never_spends_more_and_sell_never_exceeds_held() -> None:
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    bars = [_quote(bar_time, Decimal("7.0001"))]

    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        return bars[0] if (inclusive and bars[0].t >= after) or (not inclusive and bars[0].t > after) else None

    buy = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("10")),
        accepted,
        Decimal("0"),
        0,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    sell = decide_fill(
        FillOrder(side="sell", qty=Decimal("5")),
        accepted,
        Decimal("1.5"),
        0,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    fraction = decide_fill(
        FillOrder(side="sell", fraction=Decimal("0.5")),
        accepted,
        Decimal("1"),
        0,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    dust = decide_fill(
        FillOrder(side="sell", fraction=Decimal("0.0000001")),
        accepted,
        Decimal("1"),
        0,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    assert isinstance(buy, Fill)
    assert buy.qty * buy.price <= Decimal("10")
    assert isinstance(sell, Fill)
    assert sell.qty == Decimal("1.5")
    assert isinstance(fraction, Fill)
    assert fraction.qty == Decimal("0.5")
    assert isinstance(dust, Expire)
    assert dust.reason == "size rounds to zero"


def test_small_notional_still_fills() -> None:
    accepted = _utc(15, 0)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    bar = _quote(_utc(15, 1), Decimal("10"))

    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        if bar.t <= until and ((inclusive and bar.t >= after) or (not inclusive and bar.t > after)):
            return bar
        return None

    decision = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        lookup,
        _utc(15, 2),
        calendar,
        30,
    )
    assert isinstance(decision, Fill)
    assert decision.qty == Decimal("0.1")


def test_timeout_is_inclusive_of_the_boundary_and_ignores_the_overnight() -> None:
    calendar = SessionCalendar(((_utc(15, 0), _utc(16, 0)), (_utc(9, 30, day=5), _utc(16, 0, day=5))))
    accepted = _utc(15, 50)
    at_boundary = _utc(9, 50, day=5)
    past_boundary = _utc(9, 51, day=5)
    exact = _quote(at_boundary, Decimal("10"))
    later = _quote(past_boundary, Decimal("10"))

    def bars_of(*quotes: BarQuote):
        def lookup(after: datetime, until: datetime, *, inclusive: bool):
            for bar in quotes:
                if bar.t > until:
                    continue
                if (inclusive and bar.t >= after) or (not inclusive and bar.t > after):
                    return bar
            return None

        return lookup

    assert isinstance(
        decide_fill(
            FillOrder(side="buy", qty=Decimal("1")),
            accepted,
            Decimal("0"),
            0,
            bars_of(exact),
            at_boundary,
            calendar,
            30,
        ),
        Fill,
    )
    assert isinstance(
        decide_fill(
            FillOrder(side="buy", qty=Decimal("1")),
            accepted,
            Decimal("0"),
            0,
            bars_of(),
            at_boundary,
            calendar,
            30,
        ),
        Wait,
    )
    expired = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        bars_of(later),
        past_boundary,
        calendar,
        30,
    )
    assert isinstance(expired, Expire)
    assert expired.reason == "timeout"
    assert calendar.trading_minutes(accepted, _utc(16, 0)) == 10
    assert calendar.trading_minutes(accepted, _utc(9, 0, day=5)) == 10


def test_early_close_stops_at_13_00_and_carries_the_rest() -> None:
    calendar = _early_close()
    accepted = _et(2026, 1, 2, 12, 50)
    late = _et(2026, 1, 2, 13, 5)
    carry = _et(2026, 1, 5, 9, 50)
    assert calendar.trading_minutes(accepted, _et(2026, 1, 2, 13, 0)) == 10
    assert calendar.trading_minutes(accepted, late) == 10
    assert calendar.trading_minutes(accepted, carry) == 30
    assert calendar.is_open(late) is False

    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        for bar in (_quote(late, Decimal("4")), _quote(carry, Decimal("6"))):
            if bar.t > until:
                continue
            if (inclusive and bar.t >= after) or (not inclusive and bar.t > after):
                return bar
        return None

    waiting = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        lookup,
        _et(2026, 1, 2, 13, 10),
        calendar,
        30,
    )
    assert isinstance(waiting, Wait)
    filled = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        lookup,
        carry,
        calendar,
        30,
    )
    assert isinstance(filled, Fill)
    assert filled.bar_time == carry
    assert filled.price == Decimal("6")


def test_missing_next_open_leaves_the_order_accepted(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _et(2026, 1, 2, 17, 0)
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("10"))
    with pytest.raises(CalendarUnavailable):
        process_order(
            session,
            order.id,
            ManualClock(accepted),
            SessionCalendar(()),
            _settings(),
        )
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")
    assert _fills_for(session, order.id) == 0


def test_process_order_fills_through_record_fill_and_waits_without_writing(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(bar_time, "10")])
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("10"))
    settings = _settings(COMMISSION_USD=Decimal("0.25"))
    assert isinstance(
        process_order(session, order.id, ManualClock(bar_time), calendar, settings),
        Fill,
    )
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("989.75")
    assert view.positions[0].qty == Decimal("1")
    assert _fills_for(session, order.id) == 1
    assert session.get(Order, order.id).status == "filled"
    process_order(session, order.id, ManualClock(bar_time), calendar, settings)
    assert _fills_for(session, order.id) == 1
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("989.75")

    waiting = _order(session, seed.portfolio_id, ticker="MSFT", game_time=accepted, notional_usd=Decimal("10"))
    cash_before = get_portfolio(session, seed.portfolio_id).portfolio.cash
    assert isinstance(
        process_order(session, waiting.id, ManualClock(_utc(15, 2)), calendar, _settings()),
        Wait,
    )
    session.expire_all()
    assert session.get(Order, waiting.id).status == "accepted"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == cash_before
    assert _fills_for(session, waiting.id) == 0


def test_halt_expires_after_the_trading_timeout(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("10"))
    decision = process_order(session, order.id, ManualClock(_utc(15, 40)), calendar, _settings())
    assert isinstance(decision, Expire)
    assert decision.reason == "timeout"
    session.expire_all()
    stored = session.get(Order, order.id)
    assert stored.status == "expired"
    assert stored.reject_reason == "timeout"
    assert _fills_for(session, order.id) == 0


def test_unaffordable_notional_is_rejected_and_a_missing_position_is_not_expired(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(bar_time, "10")])
    buy = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("1000"))
    process_order(
        session,
        buy.id,
        ManualClock(bar_time),
        calendar,
        _settings(COMMISSION_USD=Decimal("0.01")),
    )
    session.expire_all()
    stored = session.get(Order, buy.id)
    assert stored.status == "rejected"
    assert stored.reject_reason == "insufficient cash incl. commission"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")
    assert get_portfolio(session, seed.portfolio_id).positions == ()
    assert _fills_for(session, buy.id) == 0

    sell = _order(
        session,
        seed.portfolio_id,
        ticker="MSFT",
        side="sell",
        game_time=accepted,
        notional_usd=None,
        qty=Decimal("1"),
    )
    process_order(session, sell.id, ManualClock(bar_time), calendar, _settings())
    session.expire_all()
    rejected = session.get(Order, sell.id)
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "no position"
    assert _fills_for(session, sell.id) == 0


def _db_lookup(session, ticker: str):
    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        row = first_bar_after(session, ticker, after, until, inclusive=inclusive)
        if row is None:
            return None
        return BarQuote(t=row.t, open=row.o)

    return lookup


_OPEN_AT = datetime(2026, 1, 6, 14, 30, tzinfo=_UTC)
_CLOSE_AT = datetime(2026, 1, 6, 16, 0, tzinfo=_UTC)


@settings(
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    offsets=st.lists(st.integers(min_value=0, max_value=100), unique=True, min_size=0, max_size=6),
    bps=st.integers(min_value=0, max_value=50),
    notional=st.decimals(min_value=Decimal("1"), max_value=Decimal("40"), places=2),
    sell_qty=st.decimals(min_value=Decimal("0.000001"), max_value=Decimal("5"), places=6),
    side=st.sampled_from(["buy", "sell"]),
    open_price=st.decimals(min_value=Decimal("1"), max_value=Decimal("30"), places=4),
    now_delta=st.integers(min_value=1, max_value=80),
)
def test_fill_properties_and_a_refused_fill_changes_nothing(
    league_session,
    offsets: list[int],
    bps: int,
    notional: Decimal,
    sell_qty: Decimal,
    side: str,
    open_price: Decimal,
    now_delta: int,
) -> None:
    session, seed, _engine = league_session
    nested = session.begin_nested()
    try:
        accepted = _OPEN_AT + timedelta(minutes=10)
        now = accepted + timedelta(minutes=now_delta)
        calendar = SessionCalendar(((_OPEN_AT, _CLOSE_AT),))
        quotes = [_quote(accepted + timedelta(minutes=offset), open_price) for offset in offsets]
        closed_at = _CLOSE_AT + timedelta(minutes=5)
        if all(bar.t != closed_at for bar in quotes):
            quotes.append(_quote(closed_at, open_price))

        def lookup(after: datetime, until: datetime, *, inclusive: bool):
            for bar in sorted(quotes, key=lambda item: item.t):
                if bar.t > until:
                    continue
                if (inclusive and bar.t >= after) or (not inclusive and bar.t > after):
                    return bar
            return None

        order = FillOrder(
            side=side,
            notional_usd=notional if side == "buy" else None,
            qty=sell_qty if side == "sell" else None,
        )
        held = Decimal("5")
        decision = decide_fill(order, accepted, held, bps, lookup, now, calendar, 30)
        if isinstance(decision, Fill):
            assert calendar.is_open(decision.bar_time)
            assert accepted < decision.bar_time <= now
            assert calendar.trading_minutes(accepted, decision.bar_time) <= 30
            raw = open_price * (1 + Decimal(bps) / Decimal("10000"))
            if side == "sell":
                raw = open_price * (1 - Decimal(bps) / Decimal("10000"))
            _ignored, expected = quantize_fill(side, Decimal("1"), raw)
            assert decision.price == expected
            if side == "buy":
                assert decision.qty * decision.price <= notional
            else:
                assert decision.qty <= held

        upsert_bars(
            session,
            [
                BarInput(
                    ticker="AAPL",
                    t=bar.t,
                    o=bar.open,
                    h=bar.open,
                    l=bar.open,
                    c=bar.open,
                    v=1,
                    feed="iex",
                )
                for bar in quotes
            ],
        )
        if side == "buy":
            stored = create_order(
                session,
                portfolio_id=seed.portfolio_id,
                ticker="AAPL",
                side="buy",
                source="player",
                status="accepted",
                game_time=accepted,
                notional_usd=notional,
            )
            process_order(session, stored.id, ManualClock(now), calendar, _settings(SLIPPAGE_BPS=bps))
            first = _fills_for(session, stored.id)
            process_order(session, stored.id, ManualClock(now), calendar, _settings(SLIPPAGE_BPS=bps))
            assert _fills_for(session, stored.id) == first
            assert first in {0, 1}
            if isinstance(decision, Fill):
                assert first == 1

        session.expire_all()
        cash_before = get_portfolio(session, seed.portfolio_id).portfolio.cash
        fills_before = _count(session, FillRow, seed.portfolio_id)
        positions_before = _count(session, Position, seed.portfolio_id)
        refused = create_order(
            session,
            portfolio_id=seed.portfolio_id,
            ticker="AAPL",
            side="buy",
            source="player",
            status="accepted",
            game_time=accepted,
            notional_usd=cash_before,
        )
        visible = _quote(accepted + timedelta(minutes=1), Decimal("10"))
        upsert_bars(
            session,
            [
                BarInput(
                    ticker="AAPL",
                    t=visible.t,
                    o=visible.open,
                    h=visible.open,
                    l=visible.open,
                    c=visible.open,
                    v=1,
                    feed="iex",
                )
            ],
        )
        process_order(
            session,
            refused.id,
            ManualClock(visible.t),
            calendar,
            _settings(COMMISSION_USD=Decimal("1")),
        )
        session.expire_all()
        assert get_portfolio(session, seed.portfolio_id).portfolio.cash == cash_before
        assert _count(session, FillRow, seed.portfolio_id) == fills_before
        assert _count(session, Position, seed.portfolio_id) == positions_before
        assert session.get(Order, refused.id).status == "rejected"
    finally:
        nested.rollback()


def _window_lookup(bars: list[BarQuote]):
    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        for bar in sorted(bars, key=lambda item: item.t):
            if bar.t > until:
                continue
            if (inclusive and bar.t >= after) or (not inclusive and bar.t > after):
                return bar
        return None

    return lookup


def test_bars_at_or_before_t_and_after_now_are_not_fills(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    now = _utc(15, 2)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(
        session,
        [
            _bar(accepted - timedelta(minutes=1), "1"),
            _bar(accepted, "2"),
            _bar(now - timedelta(minutes=1), "3"),
            _bar(now, "4"),
            _bar(now + timedelta(minutes=1), "9"),
        ],
    )
    early = process_order(
        session,
        _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("4")).id,
        ManualClock(accepted - timedelta(minutes=1)),
        calendar,
        _settings(),
    )
    assert isinstance(early, Wait)
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")

    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("4"))
    decision = process_order(session, order.id, ManualClock(now), calendar, _settings())
    assert isinstance(decision, Fill)
    assert decision.bar_time == now - timedelta(minutes=1)
    assert decision.price == Decimal("3")
    session.expire_all()
    stored = session.scalar(select(FillRow).where(FillRow.order_id == order.id))
    assert stored is not None
    assert stored.bar_time == now - timedelta(minutes=1)
    assert stored.price == Decimal("3")

    def stale(after: datetime, until: datetime, *, inclusive: bool):
        del after, inclusive
        return _quote(until + timedelta(minutes=1), Decimal("9"))

    assert isinstance(
        decide_fill(
            FillOrder(side="buy", qty=Decimal("1")),
            accepted,
            Decimal("0"),
            0,
            stale,
            now,
            calendar,
            30,
        ),
        Wait,
    )


def test_open_and_close_instants_bound_the_session(league_session) -> None:
    session, seed, _engine = league_session
    regular = _regular_days()
    opened = _et(2026, 1, 2, 9, 30)
    one_minute = _et(2026, 1, 2, 9, 31)
    closed = _et(2026, 1, 2, 16, 0)
    before_open = _et(2026, 1, 5, 9, 29)
    next_open = _et(2026, 1, 5, 9, 30)
    upsert_bars(
        session,
        [
            _bar(opened, "2"),
            _bar(one_minute, "3"),
            _bar(closed, "4"),
            _bar(before_open, "6"),
            _bar(next_open, "7"),
        ],
    )
    at_open = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        opened,
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        one_minute,
        regular,
        30,
    )
    assert isinstance(at_open, Fill)
    assert at_open.bar_time == one_minute
    assert at_open.price == Decimal("3")

    at_close = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        _et(2026, 1, 2, 15, 59),
        Decimal("0"),
        0,
        _db_lookup(session, "AAPL"),
        closed,
        regular,
        30,
    )
    assert isinstance(at_close, Wait)
    assert regular.is_open(closed) is False
    assert regular.next_open(closed) == next_open

    overnight = _order(
        session,
        seed.portfolio_id,
        game_time=_et(2026, 1, 2, 17, 0),
        status="queued",
        notional_usd=None,
        qty=Decimal("1"),
    )
    filled = process_order(session, overnight.id, ManualClock(next_open), regular, _settings())
    assert isinstance(filled, Fill)
    assert filled.bar_time == next_open
    assert filled.price == Decimal("7")

    half = _early_close()
    boundary = _et(2026, 1, 2, 13, 0)
    upsert_bars(session, [_bar(boundary, "5", ticker="MSFT"), _bar(next_open, "7", ticker="MSFT")])
    only_the_close = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        _et(2026, 1, 2, 12, 59),
        Decimal("0"),
        0,
        _db_lookup(session, "MSFT"),
        boundary,
        half,
        30,
    )
    assert isinstance(only_the_close, Wait)
    carried = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        _et(2026, 1, 2, 12, 50),
        Decimal("0"),
        0,
        _db_lookup(session, "MSFT"),
        next_open,
        half,
        30,
    )
    assert isinstance(carried, Fill)
    assert carried.bar_time == next_open
    assert carried.price == Decimal("7")


def test_timeout_crosses_a_weekend_and_a_holiday(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _et(2026, 1, 2, 15, 30)
    friday_close = _et(2026, 1, 2, 16, 0)
    sunday = _et(2026, 1, 4, 12, 0)
    holiday = _et(2026, 1, 5, 10, 0)
    tuesday_open = _et(2026, 1, 6, 9, 30)
    tuesday_late = _et(2026, 1, 6, 9, 31)
    calendar = SessionCalendar(
        (
            (_et(2026, 1, 2, 9, 30), friday_close),
            (_et(2026, 1, 6, 9, 30), _et(2026, 1, 6, 16, 0)),
        )
    )
    assert calendar.trading_minutes(accepted, friday_close) == 30
    assert calendar.trading_minutes(accepted, sunday) == 30
    assert calendar.trading_minutes(accepted, holiday) == 30
    assert calendar.trading_minutes(accepted, tuesday_open) == 30
    assert calendar.trading_minutes(accepted, tuesday_late) == 31
    upsert_bars(
        session,
        [
            _bar(_et(2026, 1, 3, 12, 0), "1", ticker="QAFL"),
            _bar(holiday, "2", ticker="QAFL"),
            _bar(tuesday_open, "3", ticker="QAFL"),
            _bar(tuesday_late, "4", ticker="QAFM"),
        ],
    )
    waiting = _order(
        session,
        seed.portfolio_id,
        ticker="QAFL",
        status="queued",
        game_time=accepted,
        notional_usd=None,
        qty=Decimal("1"),
    )
    assert isinstance(
        process_order(session, waiting.id, ManualClock(sunday), calendar, _settings()),
        Wait,
    )
    session.expire_all()
    assert session.get(Order, waiting.id).status == "queued"
    filled = process_order(session, waiting.id, ManualClock(tuesday_open), calendar, _settings())
    assert isinstance(filled, Fill)
    assert filled.bar_time == tuesday_open
    assert filled.price == Decimal("3")
    assert calendar.is_open(filled.bar_time)

    expired_order = _order(
        session,
        seed.portfolio_id,
        ticker="QAFM",
        status="queued",
        game_time=accepted,
        notional_usd=None,
        qty=Decimal("1"),
    )
    expired = process_order(session, expired_order.id, ManualClock(tuesday_late), calendar, _settings())
    assert isinstance(expired, Expire)
    assert expired.reason == "timeout"
    session.expire_all()
    stored = session.get(Order, expired_order.id)
    assert stored.status == "expired"
    assert stored.reject_reason == "timeout"


def test_slippage_at_zero_and_fifty_basis_points_matches_house_rounding(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    first = _utc(15, 1)
    second = _utc(15, 2)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(first, "10.0001"), _bar(second, "10.0001")])
    expected = {}
    for bps in (0, 50):
        raw_buy = Decimal("10.0001") * (1 + Decimal(bps) / Decimal("10000"))
        raw_sell = Decimal("10.0001") * (1 - Decimal(bps) / Decimal("10000"))
        _ignored, expected[("buy", bps)] = quantize_fill("buy", Decimal("1"), raw_buy)
        _ignored, expected[("sell", bps)] = quantize_fill("sell", Decimal("1"), raw_sell)
        assert expected[("buy", bps)] >= Decimal("10.0001")
        assert expected[("sell", bps)] <= Decimal("10.0001")

    for bps, notional in ((0, Decimal("10")), (50, Decimal("20"))):
        order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=notional)
        decision = process_order(
            session,
            order.id,
            ManualClock(first),
            calendar,
            _settings(SLIPPAGE_BPS=bps),
        )
        assert isinstance(decision, Fill)
        assert decision.price == expected[("buy", bps)]
        assert decision.qty * decision.price <= notional
        stored = session.scalar(select(FillRow).where(FillRow.order_id == order.id))
        assert stored.price == expected[("buy", bps)]
        assert stored.slippage_bps == Decimal(bps)

    pure = _window_lookup([_quote(first, Decimal("10.00015"))])
    for bps in (0, 50):
        sell = decide_fill(
            FillOrder(side="sell", qty=Decimal("1")),
            accepted,
            Decimal("1"),
            bps,
            pure,
            first,
            calendar,
            30,
        )
        raw = Decimal("10.00015") * (1 - Decimal(bps) / Decimal("10000"))
        _ignored, price = quantize_fill("sell", Decimal("1"), raw)
        assert isinstance(sell, Fill)
        assert sell.price == price

    cash_before_sells = get_portfolio(session, seed.portfolio_id).portfolio.cash
    for bps in (0, 50):
        order = _order(
            session,
            seed.portfolio_id,
            side="sell",
            game_time=first,
            notional_usd=None,
            qty=Decimal("0.1"),
        )
        decision = process_order(
            session,
            order.id,
            ManualClock(second),
            calendar,
            _settings(SLIPPAGE_BPS=bps),
        )
        assert isinstance(decision, Fill)
        assert decision.price == expected[("sell", bps)]
        assert decision.qty == Decimal("0.1")
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash > cash_before_sells


def test_notional_below_one_share_quantum_expires(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(bar_time, "250")])
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("0.0001"))
    decision = process_order(session, order.id, ManualClock(bar_time), calendar, _settings())
    assert isinstance(decision, Expire)
    assert decision.reason == "size rounds to zero"
    session.expire_all()
    stored = session.get(Order, order.id)
    assert stored.status == "expired"
    assert stored.reject_reason == "size rounds to zero"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")
    assert _fills_for(session, order.id) == 0

    dust = decide_fill(
        FillOrder(side="buy", notional_usd=Decimal("0.0001")),
        accepted,
        Decimal("0"),
        50,
        _window_lookup([_quote(bar_time, Decimal("100"))]),
        bar_time,
        calendar,
        30,
    )
    assert isinstance(dust, Expire)
    assert dust.reason == "size rounds to zero"


def _ignores_the_window(bars: list[BarQuote]):
    """Return the bars in order and ignore the caller's after/until window."""
    index = {"at": 0}

    def lookup(after: datetime, until: datetime, *, inclusive: bool):
        del after, until, inclusive
        if index["at"] >= len(bars):
            return None
        bar = bars[index["at"]]
        index["at"] += 1
        return bar

    return lookup


def test_a_wrong_lookup_cannot_fill_outside_the_window() -> None:
    accepted = _utc(15, 0)
    now = _utc(15, 10)
    valid = _quote(_utc(15, 5), Decimal("6"))
    calendar = SessionCalendar(((_utc(14, 0), _utc(15, 3)), (_utc(15, 4), _utc(21, 0))))
    decision = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        _ignores_the_window(
            [
                _quote(_utc(14, 59), Decimal("1")),
                _quote(accepted, Decimal("2")),
                _quote(_utc(15, 3), Decimal("3")),
                valid,
                _quote(now + timedelta(minutes=1), Decimal("9")),
            ]
        ),
        now,
        calendar,
        30,
    )
    assert isinstance(decision, Fill)
    assert decision.bar_time == valid.t
    assert decision.price == Decimal("6")
    assert calendar.is_open(decision.bar_time)

    refused = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        accepted,
        Decimal("0"),
        0,
        _ignores_the_window(
            [
                _quote(_utc(14, 59), Decimal("1")),
                _quote(accepted, Decimal("2")),
                _quote(_utc(15, 3), Decimal("3")),
                _quote(now + timedelta(minutes=1), Decimal("9")),
            ]
        ),
        now,
        calendar,
        30,
    )
    assert isinstance(refused, Wait)

    opened = _et(2026, 1, 5, 9, 30)
    closed = decide_fill(
        FillOrder(side="buy", qty=Decimal("1")),
        _et(2026, 1, 2, 17, 0),
        Decimal("0"),
        0,
        _ignores_the_window(
            [
                _quote(_et(2026, 1, 2, 17, 30), Decimal("4")),
                _quote(_et(2026, 1, 5, 9, 29), Decimal("5")),
                _quote(opened, Decimal("8")),
            ]
        ),
        opened,
        _regular_days(),
        30,
    )
    assert isinstance(closed, Fill)
    assert closed.bar_time == opened
    assert closed.price == Decimal("8")


def test_bad_fraction_and_a_zero_price_expire() -> None:
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    lookup = _window_lookup([_quote(bar_time, Decimal("10"))])
    zero = decide_fill(
        FillOrder(side="sell", fraction=Decimal("0")),
        accepted,
        Decimal("2"),
        0,
        lookup,
        bar_time,
        calendar,
        30,
    )
    huge = decide_fill(
        FillOrder(side="sell", fraction=Decimal("1.1")),
        accepted,
        Decimal("2"),
        0,
        lookup,
        bar_time,
        calendar,
        30,
    )
    whole = decide_fill(
        FillOrder(side="sell", fraction=Decimal("1")),
        accepted,
        Decimal("2"),
        0,
        lookup,
        bar_time,
        calendar,
        30,
    )
    unsized = decide_fill(
        FillOrder(side="sell"),
        accepted,
        Decimal("2"),
        0,
        lookup,
        bar_time,
        calendar,
        30,
    )
    worthless = decide_fill(
        FillOrder(side="sell", qty=Decimal("1")),
        accepted,
        Decimal("1"),
        0,
        _window_lookup([_quote(bar_time, Decimal("0.00005"))]),
        bar_time,
        calendar,
        30,
    )
    assert isinstance(zero, Expire)
    assert zero.reason == "bad fraction"
    assert isinstance(huge, Expire)
    assert huge.reason == "bad fraction"
    assert isinstance(whole, Fill)
    assert whole.qty == Decimal("2")
    assert isinstance(unsized, Expire)
    assert unsized.reason == "unsized"
    assert isinstance(worthless, Expire)
    assert worthless.reason == "price rounds to zero"


def test_sell_after_the_position_shrinks_is_capped_at_the_remainder(league_session) -> None:
    session, seed, _engine = league_session
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    buy_at = _utc(15, 1)
    sold_at = _utc(15, 2)
    rest_at = _utc(15, 3)
    upsert_bars(session, [_bar(buy_at, "10"), _bar(sold_at, "10"), _bar(rest_at, "11")])
    buy = _order(session, seed.portfolio_id, game_time=_utc(15, 0), notional_usd=Decimal("100"))
    assert isinstance(process_order(session, buy.id, ManualClock(buy_at), calendar, _settings()), Fill)
    partial = _order(
        session,
        seed.portfolio_id,
        side="sell",
        game_time=buy_at,
        notional_usd=None,
        qty=Decimal("6"),
    )
    assert isinstance(process_order(session, partial.id, ManualClock(sold_at), calendar, _settings()), Fill)
    session.expire_all()
    assert get_portfolio(session, seed.portfolio_id).positions[0].qty == Decimal("4")
    resting = _order(
        session,
        seed.portfolio_id,
        side="sell",
        game_time=sold_at,
        notional_usd=None,
        qty=Decimal("10"),
    )
    decision = process_order(session, resting.id, ManualClock(rest_at), calendar, _settings())
    assert isinstance(decision, Fill)
    assert decision.qty == Decimal("4")
    assert decision.price == Decimal("11")
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions == ()
    assert view.portfolio.cash == Decimal("1004")
    stored = session.scalar(select(FillRow).where(FillRow.order_id == resting.id))
    assert stored.qty == Decimal("4")


def test_commission_pushes_a_smaller_notional_over_cash(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(bar_time, "10")])
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("999.98"))
    process_order(
        session,
        order.id,
        ManualClock(bar_time),
        calendar,
        _settings(COMMISSION_USD=Decimal("0.03")),
    )
    session.expire_all()
    stored = session.get(Order, order.id)
    assert stored.status == "rejected"
    assert stored.reject_reason == "insufficient cash incl. commission"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")
    assert get_portfolio(session, seed.portfolio_id).positions == ()
    assert _fills_for(session, order.id) == 0

    queued = _order(
        session,
        seed.portfolio_id,
        status="queued",
        game_time=accepted,
        notional_usd=Decimal("1000"),
    )
    process_order(
        session,
        queued.id,
        ManualClock(bar_time),
        calendar,
        _settings(COMMISSION_USD=Decimal("0.01")),
    )
    session.expire_all()
    rejected = session.get(Order, queued.id)
    assert rejected.status == "rejected"
    assert rejected.reject_reason == "insufficient cash incl. commission"
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")
    assert process_order(session, queued.id, ManualClock(bar_time), calendar, _settings()) is None


def test_process_order_repeats_do_not_write_again(league_session) -> None:
    session, seed, _engine = league_session
    accepted = _utc(15, 0)
    bar_time = _utc(15, 1)
    calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
    upsert_bars(session, [_bar(bar_time, "10")])
    settings = _settings(COMMISSION_USD=Decimal("0.25"))
    order = _order(session, seed.portfolio_id, game_time=accepted, notional_usd=Decimal("10"))
    assert isinstance(process_order(session, order.id, ManualClock(bar_time), calendar, settings), Fill)
    for _ in range(4):
        assert process_order(session, order.id, ManualClock(bar_time), calendar, settings) is None
    session.expire_all()
    assert _fills_for(session, order.id) == 1
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("989.75")

    waiting = _order(
        session,
        seed.portfolio_id,
        ticker="MSFT",
        status="queued",
        game_time=accepted,
        notional_usd=Decimal("10"),
    )
    for _ in range(4):
        assert isinstance(
            process_order(session, waiting.id, ManualClock(_utc(15, 2)), calendar, _settings()),
            Wait,
        )
    session.expire_all()
    assert session.get(Order, waiting.id).status == "queued"
    assert _fills_for(session, waiting.id) == 0

    halted = _order(
        session,
        seed.portfolio_id,
        ticker="NVDA",
        game_time=accepted,
        notional_usd=Decimal("10"),
    )
    assert isinstance(
        process_order(session, halted.id, ManualClock(_utc(15, 40)), calendar, _settings()),
        Expire,
    )
    assert process_order(session, halted.id, ManualClock(_utc(15, 41)), calendar, _settings()) is None
    session.expire_all()
    assert session.get(Order, halted.id).status == "expired"
    assert session.get(Order, halted.id).reject_reason == "timeout"


def _review_cleanup(engine, user_id: int | None, before: int, threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(15)
    still_alive = any(thread.is_alive() for thread in threads)
    wipe_error: Exception | None = None
    if user_id is not None:
        try:
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM league.bars WHERE ticker IN ('QAFL', 'QAFM')"))
            _wipe_user(engine, user_id)
        except Exception as exc:
            logging.getLogger("tests.unit.league").exception(
                "failed to remove committed review rows for user %s", user_id
            )
            wipe_error = exc
    try:
        after = _user_count(engine)
    finally:
        engine.dispose()
    assert after == before
    assert not still_alive
    if wipe_error is not None:
        raise wipe_error


def test_two_sessions_cannot_fill_one_order_twice() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    threads: list[threading.Thread] = []
    try:
        accepted = _utc(15, 0)
        bar_time = _utc(15, 1)
        calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM league.bars WHERE ticker = 'QAFL'"))
                seed = _seed(session)
                user_id = seed.user_id
                upsert_bars(session, [_bar(bar_time, "10", ticker="QAFL")])
                order = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=accepted,
                    notional_usd=Decimal("10"),
                )
                order_id = order.id
                portfolio_id = seed.portfolio_id
        outcomes: list[str] = []

        def _attempt() -> None:
            with factory() as session:
                try:
                    with session.begin():
                        session.execute(text("SET LOCAL lock_timeout = '5s'"))
                        result = process_order(
                            session,
                            order_id,
                            ManualClock(bar_time),
                            calendar,
                            _settings(),
                        )
                    outcomes.append("filled" if isinstance(result, Fill) else type(result).__name__)
                except OrderNotFillable:
                    outcomes.append("blocked")
                except Exception as exc:  # pragma: no cover - failure detail
                    outcomes.append(f"error:{type(exc).__name__}:{exc}")

        threads = [threading.Thread(target=_attempt), threading.Thread(target=_attempt)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        with engine.connect() as connection:
            fills = connection.execute(
                text("SELECT count(*) FROM league.fills WHERE order_id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            status = connection.execute(
                text("SELECT status FROM league.orders WHERE id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
        assert fills == 1, outcomes
        assert Decimal(cash) == Decimal("990"), outcomes
        assert status == "filled", outcomes
        # The loser re-reads the committed row under the lock and returns
        # without writing. An exception here is a stale identity-map fill.
        assert sorted(outcomes) == ["NoneType", "filled"], outcomes
    finally:
        _review_cleanup(engine, user_id, before, threads)


def test_two_sessions_rejecting_one_queued_order_leave_it_rejected() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    threads: list[threading.Thread] = []
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                seed = _seed(session)
                user_id = seed.user_id
                order = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    side="sell",
                    status="queued",
                    game_time=_utc(17, 0),
                    notional_usd=None,
                    qty=Decimal("1"),
                )
                order_id = order.id
                portfolio_id = seed.portfolio_id
        errors: list[str] = []

        def _attempt() -> None:
            with factory() as session:
                try:
                    with session.begin():
                        session.execute(text("SET LOCAL lock_timeout = '5s'"))
                        process_order(
                            session,
                            order_id,
                            ManualClock(_utc(17, 0)),
                            _regular_days(),
                            _settings(),
                        )
                except Exception as exc:  # pragma: no cover - failure detail
                    errors.append(f"{type(exc).__name__}:{exc}")

        threads = [threading.Thread(target=_attempt), threading.Thread(target=_attempt)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        assert errors == []
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT status, reject_reason FROM league.orders WHERE id = :order_id"),
                {"order_id": order_id},
            ).one()
            fills = connection.execute(
                text("SELECT count(*) FROM league.fills WHERE order_id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
        assert row.status == "rejected"
        assert row.reject_reason == "no position"
        assert fills == 0
        assert Decimal(cash) == Decimal("1000")
    finally:
        _review_cleanup(engine, user_id, before, threads)


def test_queued_reject_is_not_visible_to_another_transaction() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    reader: threading.Thread | None = None
    resume = threading.Event()
    try:
        accepted = _utc(17, 0)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                seed = _seed(session)
                user_id = seed.user_id
                order = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    side="sell",
                    status="queued",
                    game_time=accepted,
                    notional_usd=None,
                    qty=Decimal("1"),
                )
                order_id = order.id
                portfolio_id = seed.portfolio_id
        seen: list[str] = []
        other: dict[str, str] = {}
        paused = threading.Event()

        def _reader() -> None:
            try:
                if not paused.wait(5):
                    other["error"] = f"not paused: {seen}"
                    return
                with engine.connect() as connection:
                    other["during"] = connection.execute(
                        text("SELECT status FROM league.orders WHERE id = :order_id"),
                        {"order_id": order_id},
                    ).scalar_one()
            except Exception as exc:  # pragma: no cover - failure detail
                other["error"] = f"{type(exc).__name__}:{exc}"
            finally:
                resume.set()

        reader = threading.Thread(target=_reader)
        reader.start()
        with factory() as session:

            def _watch(sess, _context) -> None:
                for obj in list(sess.identity_map.values()):
                    if isinstance(obj, Order) and obj.id == order_id:
                        seen.append(obj.status)
                        if obj.status == "rejected" and not resume.is_set():
                            paused.set()
                            assert resume.wait(5), seen

            event.listen(session, "after_flush", _watch)
            try:
                with session.begin():
                    process_order(
                        session,
                        order_id,
                        ManualClock(accepted),
                        _regular_days(),
                        _settings(),
                    )
            finally:
                event.remove(session, "after_flush", _watch)
        reader.join(10)
        assert other.get("error") is None
        assert other.get("during") == "queued"
        assert "accepted" not in seen
        assert "rejected" in seen
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, reject_reason FROM league.orders WHERE id = :order_id"
                ),
                {"order_id": order_id},
            ).one()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
        assert row.status == "rejected"
        assert row.reject_reason == "no position"
        assert Decimal(cash) == Decimal("1000")
    finally:
        resume.set()
        if reader is not None:
            reader.join(5)
        _review_cleanup(engine, user_id, before, [reader] if reader is not None else [])


def _race(engine, order_ids: list[uuid.UUID], when: datetime, calendar: SessionCalendar) -> list[str]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    outcomes: list[str] = []
    gate = threading.Barrier(len(order_ids))

    def _attempt(order_id: uuid.UUID) -> None:
        with factory() as session:
            try:
                with session.begin():
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    gate.wait(5)
                    result = process_order(
                        session,
                        order_id,
                        ManualClock(when),
                        calendar,
                        _settings(),
                    )
                outcomes.append("filled" if isinstance(result, Fill) else type(result).__name__)
            except Exception as exc:  # pragma: no cover - failure detail
                outcomes.append(f"error:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=_attempt, args=(order_id,)) for order_id in order_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert all(not thread.is_alive() for thread in threads), outcomes
    return outcomes


def test_three_sessions_fill_one_order_once() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    try:
        accepted = _utc(15, 0)
        bar_time = _utc(15, 1)
        calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM league.bars WHERE ticker = 'QAFL'"))
                seed = _seed(session)
                user_id = seed.user_id
                upsert_bars(session, [_bar(bar_time, "10", ticker="QAFL")])
                order = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=accepted,
                    notional_usd=Decimal("10"),
                )
                order_id = order.id
                portfolio_id = seed.portfolio_id
        outcomes = _race(engine, [order_id, order_id, order_id], bar_time, calendar)
        with engine.connect() as connection:
            fills = connection.execute(
                text("SELECT count(*) FROM league.fills WHERE order_id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            status = connection.execute(
                text("SELECT status FROM league.orders WHERE id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
        assert fills == 1, outcomes
        assert Decimal(cash) == Decimal("990"), outcomes
        assert status == "filled", outcomes
        assert sorted(outcomes) == ["NoneType", "NoneType", "filled"], outcomes
    finally:
        _review_cleanup(engine, user_id, before, [])


def test_two_orders_cannot_spend_the_same_cash() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    try:
        accepted = _utc(15, 0)
        bar_time = _utc(15, 1)
        calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM league.bars WHERE ticker = 'QAFL'"))
                seed = _seed(session)
                user_id = seed.user_id
                upsert_bars(session, [_bar(bar_time, "10", ticker="QAFL")])
                first = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=accepted,
                    notional_usd=Decimal("600"),
                )
                second = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=accepted,
                    notional_usd=Decimal("600"),
                )
                order_ids = [first.id, second.id]
                portfolio_id = seed.portfolio_id
        outcomes = _race(engine, order_ids, bar_time, calendar)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT o.status, o.reject_reason, count(f.id) AS fills
                    FROM league.orders o
                    LEFT JOIN league.fills f ON f.order_id = o.id
                    WHERE o.id IN (:first, :second)
                    GROUP BY o.id, o.status, o.reject_reason
                    """
                ),
                {"first": order_ids[0], "second": order_ids[1]},
            ).all()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            qty = connection.execute(
                text(
                    """
                    SELECT coalesce(sum(qty), 0) FROM league.positions
                    WHERE portfolio_id = :portfolio_id AND ticker = 'QAFL'
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
        assert sorted(outcomes) == ["NoneType", "filled"], outcomes
        assert Decimal(cash) == Decimal("400"), outcomes
        assert Decimal(cash) >= 0
        assert Decimal(qty) == Decimal("60"), outcomes
        statuses = sorted((row.status, row.reject_reason, row.fills) for row in rows)
        assert statuses == [
            ("filled", None, 1),
            ("rejected", "insufficient cash incl. commission", 0),
        ], outcomes
    finally:
        _review_cleanup(engine, user_id, before, [])


def test_a_sell_racing_a_buy_stays_consistent() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    try:
        calendar = SessionCalendar(((_utc(14, 0), _utc(21, 0)),))
        seeded = _utc(15, 1)
        raced = _utc(15, 2)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM league.bars WHERE ticker = 'QAFL'"))
                seed = _seed(session)
                user_id = seed.user_id
                upsert_bars(
                    session,
                    [_bar(seeded, "10", ticker="QAFL"), _bar(raced, "10", ticker="QAFL")],
                )
                opening = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=_utc(15, 0),
                    notional_usd=Decimal("40"),
                )
                assert isinstance(
                    process_order(session, opening.id, ManualClock(seeded), calendar, _settings()),
                    Fill,
                )
                buy = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    game_time=seeded,
                    notional_usd=Decimal("100"),
                )
                sell = _order(
                    session,
                    seed.portfolio_id,
                    ticker="QAFL",
                    side="sell",
                    game_time=seeded,
                    notional_usd=None,
                    qty=Decimal("10"),
                )
                buy_id = buy.id
                sell_id = sell.id
                portfolio_id = seed.portfolio_id
        outcomes = _race(engine, [buy_id, sell_id], raced, calendar)
        with engine.connect() as connection:
            buy_row = connection.execute(
                text(
                    """
                    SELECT o.status, f.qty, f.price
                    FROM league.orders o
                    LEFT JOIN league.fills f ON f.order_id = o.id
                    WHERE o.id = :order_id
                    """
                ),
                {"order_id": buy_id},
            ).one()
            sell_row = connection.execute(
                text(
                    """
                    SELECT o.status, o.reject_reason, f.qty, f.price
                    FROM league.orders o
                    LEFT JOIN league.fills f ON f.order_id = o.id
                    WHERE o.id = :order_id
                    """
                ),
                {"order_id": sell_id},
            ).one()
            cash = connection.execute(
                text("SELECT cash FROM league.portfolios WHERE id = :portfolio_id"),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
            qty = connection.execute(
                text(
                    """
                    SELECT coalesce(sum(qty), 0) FROM league.positions
                    WHERE portfolio_id = :portfolio_id AND ticker = 'QAFL'
                    """
                ),
                {"portfolio_id": portfolio_id},
            ).scalar_one()
        assert not any(item.startswith("error:") for item in outcomes), outcomes
        assert buy_row.status == "filled", outcomes
        assert Decimal(buy_row.qty) == Decimal("10")
        assert Decimal(buy_row.price) == Decimal("10")
        assert sell_row.status == "filled", outcomes
        assert Decimal(sell_row.price) == Decimal("10")
        sold = Decimal(sell_row.qty)
        assert sold in {Decimal("4"), Decimal("10")}, outcomes
        assert Decimal(qty) == Decimal("14") - sold
        assert Decimal(cash) == Decimal("960") - Decimal("100") + sold * Decimal("10")
        assert Decimal(cash) >= 0
        assert Decimal(qty) > 0
    finally:
        _review_cleanup(engine, user_id, before, [])

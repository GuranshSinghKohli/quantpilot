"""Repository writes for Week 1. The caller owns the transaction."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

import app.league.repo as repo
from app.league.models import Agent, Fill, League, Order, Player, Season
from app.league.repo import (
    BarInput,
    FillBeforeOrder,
    FillInput,
    FillNotPositive,
    InsufficientCash,
    InsufficientPosition,
    OrderNotFillable,
    OrderNotFound,
    StatusNotAllowed,
    create_order,
    create_portfolio,
    first_bar_after,
    get_portfolio,
    record_fill,
    set_order_status,
    upsert_bars,
)
from tests.unit.league.test_league_migration import _config, _engine, _url

GAME = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
BAR = datetime(2026, 1, 2, 15, 1, tzinfo=timezone.utc)
FILLED_AT = datetime(2026, 1, 2, 15, 1, 1, tzinfo=timezone.utc)
SEASON_START = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
SEASON_END = datetime(2026, 1, 30, 21, 0, tzinfo=timezone.utc)


class _Seed:
    def __init__(self, user_id: int, portfolio_id: uuid.UUID) -> None:
        self.user_id = user_id
        self.portfolio_id = portfolio_id


def _seed(session: Session) -> _Seed:
    user_id = session.execute(
        text(
            """
            INSERT INTO public.users (email, hashed_password, created_at)
            VALUES (:email, 'not-a-login', TIMESTAMPTZ '2026-01-02 14:00:00+00')
            RETURNING id
            """
        ),
        {"email": f"league-repo-{uuid.uuid4()}@local.quantpilot"},
    ).scalar_one()
    player = Player(user_id=user_id, display_name="Repo", avatar_id="avatar")
    session.add(player)
    session.flush()
    league = League(name="Repo", is_public=False, owner_id=player.id)
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        number=1,
        starts_at=SEASON_START,
        ends_at=SEASON_END,
        status="active",
    )
    session.add(season)
    session.flush()
    agent = Agent(
        player_id=player.id,
        league_id=league.id,
        name="Repo",
        risk_level="balanced",
        style="momentum",
        mode="autopilot",
        config_version=1,
    )
    session.add(agent)
    session.flush()
    portfolio = create_portfolio(
        session, agent.id, season.id, "actual", Decimal("1000")
    )
    return _Seed(user_id=user_id, portfolio_id=portfolio.id)


def _user_count(engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM public.users")).scalar_one())


def _wipe_user(engine, user_id: int) -> None:
    params = {"user_id": user_id}
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM league.fills
                WHERE order_id IN (
                    SELECT o.id FROM league.orders o
                    JOIN league.portfolios p ON p.id = o.portfolio_id
                    JOIN league.agents a ON a.id = p.agent_id
                    JOIN league.players pl ON pl.id = a.player_id
                    WHERE pl.user_id = :user_id
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.orders
                WHERE portfolio_id IN (
                    SELECT p.id FROM league.portfolios p
                    JOIN league.agents a ON a.id = p.agent_id
                    JOIN league.players pl ON pl.id = a.player_id
                    WHERE pl.user_id = :user_id
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.positions
                WHERE portfolio_id IN (
                    SELECT p.id FROM league.portfolios p
                    JOIN league.agents a ON a.id = p.agent_id
                    JOIN league.players pl ON pl.id = a.player_id
                    WHERE pl.user_id = :user_id
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.portfolios
                WHERE agent_id IN (
                    SELECT a.id FROM league.agents a
                    JOIN league.players pl ON pl.id = a.player_id
                    WHERE pl.user_id = :user_id
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.agents
                WHERE player_id IN (SELECT id FROM league.players WHERE user_id = :user_id)
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.seasons
                WHERE league_id IN (
                    SELECT id FROM league.leagues
                    WHERE owner_id IN (SELECT id FROM league.players WHERE user_id = :user_id)
                )
                """
            ),
            params,
        )
        connection.execute(
            text(
                """
                DELETE FROM league.leagues
                WHERE owner_id IN (SELECT id FROM league.players WHERE user_id = :user_id)
                """
            ),
            params,
        )
        connection.execute(
            text("DELETE FROM league.players WHERE user_id = :user_id"),
            params,
        )
        connection.execute(text("DELETE FROM public.users WHERE id = :user_id"), params)


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


def _order(session: Session, portfolio_id: uuid.UUID, **overrides) -> Order:
    values = {
        "portfolio_id": portfolio_id,
        "ticker": "AAPL",
        "side": "buy",
        "source": "player",
        "status": "accepted",
        "game_time": GAME,
        "notional_usd": Decimal("10"),
        "qty": None,
    }
    values.update(overrides)
    return create_order(session, **values)


def _fill(**overrides) -> FillInput:
    values = {
        "price": Decimal("10"),
        "qty": Decimal("1"),
        "slippage_bps": Decimal("5"),
        "bar_time": BAR,
        "filled_at": FILLED_AT,
    }
    values.update(overrides)
    return FillInput(**values)


def _fill_count(session: Session, order_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Fill).where(Fill.order_id == order_id)
        )
    )


def test_apply_fill_is_not_public() -> None:
    assert "apply_fill" not in repo.__all__
    assert "_apply_fill" not in repo.__all__
    assert not hasattr(repo, "apply_fill")
    assert not hasattr(repo, "_apply_fill")
    root = Path(repo.__file__).resolve().parent
    for path in root.glob("*.py"):
        source = path.read_text()
        assert ".commit(" not in source
        assert ".begin(" not in source


def test_record_fill_writes_fill_status_cash_and_position(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    record_fill(session, order.id, _fill(), Decimal("0"))
    session.expire_all()
    stored = session.get(Order, order.id)
    view = get_portfolio(session, seed.portfolio_id)
    assert stored is not None
    assert stored.status == "filled"
    assert _fill_count(session, order.id) == 1
    assert view.portfolio.cash == Decimal("990")
    assert len(view.positions) == 1
    assert view.positions[0].ticker == "AAPL"
    assert view.positions[0].qty == Decimal("1")
    assert view.positions[0].avg_cost == Decimal("10")


def test_record_fill_buy_and_sell_commission(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id, notional_usd=Decimal("20"))
    record_fill(
        session,
        buy.id,
        _fill(price=Decimal("10"), qty=Decimal("2")),
        Decimal("0.05"),
    )
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(
        session,
        sell.id,
        _fill(price=Decimal("10"), qty=Decimal("1")),
        Decimal("0.05"),
    )
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("989.90")
    assert len(view.positions) == 1
    assert view.positions[0].qty == Decimal("1")


def test_record_fill_rolls_back_when_position_update_fails(league_session, monkeypatch) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr("app.league.repo.orders._apply_fill", _boom)
    with pytest.raises(RuntimeError, match="forced"):
        record_fill(session, order.id, _fill(), Decimal("0"))
    session.expire_all()
    stored = session.get(Order, order.id)
    view = get_portfolio(session, seed.portfolio_id)
    assert stored is not None
    assert stored.status == "accepted"
    assert _fill_count(session, order.id) == 0
    assert view.portfolio.cash == Decimal("1000")
    assert view.positions == ()


def test_record_fill_inside_an_open_transaction(league_session) -> None:
    session, seed, engine = league_session
    assert session.in_transaction()
    order = _order(session, seed.portfolio_id, status="queued", notional_usd=Decimal("10"))
    record_fill(session, order.id, _fill(), Decimal("0"))
    assert session.in_transaction()
    assert session.get(Order, order.id).status == "filled"
    assert _fill_count(session, order.id) == 1
    with engine.connect() as other:
        visible = other.execute(
            text("SELECT count(*) FROM league.fills WHERE order_id = :order_id"),
            {"order_id": order.id},
        ).scalar_one()
    assert visible == 0


def test_set_order_status_rejects_filled(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    with pytest.raises(StatusNotAllowed):
        set_order_status(session, order.id, "filled")
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    set_order_status(session, order.id, "queued")
    set_order_status(session, order.id, "accepted")
    set_order_status(session, order.id, "rejected", reject_reason="no")
    session.expire_all()
    stored = session.get(Order, order.id)
    assert stored is not None
    assert stored.status == "rejected"
    assert stored.reject_reason == "no"


def test_create_order_rejects_filled_and_pending_approval(league_session) -> None:
    session, seed, _engine = league_session
    before = int(
        session.scalar(
            select(func.count()).select_from(Order).where(Order.portfolio_id == seed.portfolio_id)
        )
    )
    for status in ("filled", "pending_approval"):
        with pytest.raises(StatusNotAllowed):
            _order(session, seed.portfolio_id, status=status)
    after = int(
        session.scalar(
            select(func.count()).select_from(Order).where(Order.portfolio_id == seed.portfolio_id)
        )
    )
    assert after == before


def test_record_fill_rejects_an_already_filled_order(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    record_fill(session, order.id, _fill(), Decimal("0"))
    with pytest.raises(OrderNotFillable):
        record_fill(session, order.id, _fill(), Decimal("0"))
    session.expire_all()
    assert _fill_count(session, order.id) == 1
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("990")


def test_record_fill_rejects_bar_at_or_before_game_time(league_session) -> None:
    session, seed, _engine = league_session
    for bar_time in (GAME, GAME - timedelta(minutes=1)):
        order = _order(session, seed.portfolio_id, ticker=f"T{bar_time.minute}")
        with pytest.raises(FillBeforeOrder):
            record_fill(session, order.id, _fill(bar_time=bar_time), Decimal("0"))
        session.expire_all()
        assert session.get(Order, order.id).status == "accepted"
        assert _fill_count(session, order.id) == 0


def test_record_fill_rejects_non_positive_price_or_qty(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id)
    for kwargs in (
        {"price": Decimal("0")},
        {"price": Decimal("-1")},
        {"qty": Decimal("0")},
        {"qty": Decimal("-1")},
    ):
        with pytest.raises(FillNotPositive):
            record_fill(session, order.id, _fill(**kwargs), Decimal("0"))
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    assert _fill_count(session, order.id) == 0
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")


def test_cash_rounding_debits_up_and_credits_down(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id, notional_usd=Decimal("2"))
    record_fill(session, buy.id, _fill(price=Decimal("1.00001"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("998.9999")
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(session, sell.id, _fill(price=Decimal("1.00009"), qty=Decimal("1")), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.portfolio.cash == Decimal("999.9999")
    assert view.positions == ()


def test_adding_to_a_position_updates_average_cost(league_session) -> None:
    session, seed, _engine = league_session
    first = _order(session, seed.portfolio_id)
    record_fill(session, first.id, _fill(price=Decimal("1"), qty=Decimal("1")), Decimal("0"))
    second = _order(session, seed.portfolio_id, notional_usd=Decimal("2"))
    record_fill(
        session, second.id, _fill(price=Decimal("1.0001"), qty=Decimal("1")), Decimal("0")
    )
    session.expire_all()
    position = get_portfolio(session, seed.portfolio_id).positions[0]
    assert position.qty == Decimal("2")
    assert position.avg_cost == Decimal("1.0000")


def test_sell_larger_than_position_is_rejected(league_session) -> None:
    session, seed, _engine = league_session
    held = _order(session, seed.portfolio_id)
    record_fill(session, held.id, _fill(), Decimal("0"))
    too_big = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("2"))
    with pytest.raises(InsufficientPosition):
        record_fill(session, too_big.id, _fill(qty=Decimal("2")), Decimal("0"))
    naked = _order(
        session, seed.portfolio_id, ticker="MSFT", side="sell", notional_usd=None, qty=Decimal("1")
    )
    with pytest.raises(InsufficientPosition):
        record_fill(session, naked.id, _fill(), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert session.get(Order, too_big.id).status == "accepted"
    assert session.get(Order, naked.id).status == "accepted"
    assert _fill_count(session, too_big.id) == 0
    assert _fill_count(session, naked.id) == 0
    assert view.portfolio.cash == Decimal("990")
    assert len(view.positions) == 1
    assert view.positions[0].qty == Decimal("1")


def test_selling_the_full_position_deletes_the_row(league_session) -> None:
    session, seed, _engine = league_session
    buy = _order(session, seed.portfolio_id)
    record_fill(session, buy.id, _fill(), Decimal("0"))
    sell = _order(session, seed.portfolio_id, side="sell", notional_usd=None, qty=Decimal("1"))
    record_fill(session, sell.id, _fill(), Decimal("0"))
    session.expire_all()
    view = get_portfolio(session, seed.portfolio_id)
    assert view.positions == ()
    assert view.portfolio.cash == Decimal("1000")


def test_buy_that_exceeds_cash_is_rejected(league_session) -> None:
    session, seed, _engine = league_session
    order = _order(session, seed.portfolio_id, notional_usd=Decimal("2000"))
    with pytest.raises(InsufficientCash):
        record_fill(session, order.id, _fill(price=Decimal("10"), qty=Decimal("200")), Decimal("0"))
    session.expire_all()
    assert session.get(Order, order.id).status == "accepted"
    assert _fill_count(session, order.id) == 0
    assert get_portfolio(session, seed.portfolio_id).portfolio.cash == Decimal("1000")


def test_first_bar_after_skips_gaps_and_bounds(league_session) -> None:
    session, _seed, _engine = league_session
    t0 = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 2, 15, 2, tzinfo=timezone.utc)
    t5 = datetime(2026, 1, 2, 15, 5, tzinfo=timezone.utc)

    def _bar(ticker: str, when: datetime, close: str) -> BarInput:
        return BarInput(
            ticker=ticker,
            t=when,
            o=Decimal("10"),
            h=Decimal("10"),
            l=Decimal("10"),
            c=Decimal(close),
            v=1,
            feed="iex",
        )

    assert upsert_bars(session, [_bar("AAPL", t0, "10"), _bar("AAPL", t2, "12"), _bar("MSFT", t2, "11")]) == 3
    found = first_bar_after(session, "AAPL", t0, t5)
    assert found is not None
    assert found.ticker == "AAPL"
    assert found.t == t2
    assert first_bar_after(session, "AAPL", t0, t0) is None
    assert first_bar_after(session, "AAPL", t2, t5) is None
    assert upsert_bars(session, [_bar("AAPL", t5, "13")]) == 1
    at_until = first_bar_after(session, "AAPL", t2, t5)
    assert at_until is not None
    assert at_until.t == t5
    other = first_bar_after(session, "MSFT", t0, t2)
    assert other is not None
    assert other.c == Decimal("11")


def test_upsert_bars_is_idempotent(league_session) -> None:
    session, _seed, _engine = league_session
    when = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    first = BarInput(
        ticker="AAPL",
        t=when,
        o=Decimal("1"),
        h=Decimal("2"),
        l=Decimal("0.5"),
        c=Decimal("1.5"),
        v=10,
        feed="iex",
    )
    replacement = BarInput(
        ticker="AAPL",
        t=when,
        o=Decimal("9"),
        h=Decimal("9"),
        l=Decimal("9"),
        c=Decimal("9"),
        v=99,
        feed="sip",
    )
    assert upsert_bars(session, [first]) == 1
    assert upsert_bars(session, [replacement]) == 0
    stored = first_bar_after(session, "AAPL", when - timedelta(minutes=1), when)
    assert stored is not None
    assert stored.o == Decimal("1")
    assert stored.h == Decimal("2")
    assert stored.l == Decimal("0.5")
    assert stored.c == Decimal("1.5")
    assert stored.v == 10
    assert stored.feed == "iex"


def test_missing_order_is_rejected(league_session) -> None:
    session, _seed, _engine = league_session
    with pytest.raises(OrderNotFound):
        record_fill(session, uuid.uuid4(), _fill(), Decimal("0"))


def test_two_sessions_one_fill_wins() -> None:
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    user_id = None
    order_id = None
    threads: list[threading.Thread] = []
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            with session.begin():
                seed = _seed(session)
                user_id = seed.user_id
                order = _order(session, seed.portfolio_id)
                order_id = order.id
        outcomes: list[str] = []

        def _attempt() -> None:
            with factory() as session:
                try:
                    with session.begin():
                        session.execute(text("SET LOCAL lock_timeout = '5s'"))
                        record_fill(session, order_id, _fill(), Decimal("0"))
                    outcomes.append("ok")
                except OrderNotFillable:
                    outcomes.append("rejected")
                except Exception as exc:  # pragma: no cover - failure detail
                    outcomes.append(f"error:{type(exc).__name__}:{exc}")

        threads = [threading.Thread(target=_attempt), threading.Thread(target=_attempt)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["ok", "rejected"]
        with engine.connect() as connection:
            fills = connection.execute(
                text("SELECT count(*) FROM league.fills WHERE order_id = :order_id"),
                {"order_id": order_id},
            ).scalar_one()
        assert fills == 1
    finally:
        for thread in threads:
            thread.join(15)
        still_alive = any(thread.is_alive() for thread in threads)
        wipe_error: Exception | None = None
        if user_id is not None:
            try:
                _wipe_user(engine, user_id)
            except Exception as exc:
                logging.getLogger("tests.unit.league").exception(
                    "failed to remove committed public.users row %s", user_id
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

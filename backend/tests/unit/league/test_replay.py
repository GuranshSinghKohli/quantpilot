"""One-day replay. Synthetic bars only. The real 2026-11-24 bars are not read."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from sqlalchemy.orm import Session, sessionmaker

from app.league.config import LeagueSettings
from app.league.repo import BarInput, latest_known_prices, upsert_bars
from app.league.sim.calendar import SessionCalendar
from app.league.sim.replay import ReplayRefused, replay_day, require_session
from app.league.sim.xnys import XNYSCalendar
from app.league.universe import Listing, Universe
from tests.unit.league.test_league_migration import _config, _engine, _url
from tests.unit.league.test_league_repo import _user_count

_ET = ZoneInfo("America/New_York")
_OPENED = datetime(2031, 6, 3, 9, 30, tzinfo=_ET)
_CLOSED = datetime(2031, 6, 3, 16, 0, tzinfo=_ET)
_DROP = datetime(2031, 6, 3, 11, 0, tzinfo=_ET)
_NAMES = ("AAPL", "MSFT", "SPY")
_REPLAY = Path(__file__).resolve().parents[3] / "app" / "league" / "sim" / "replay.py"


def _settings() -> LeagueSettings:
    return LeagueSettings(
        STARTING_CASH=Decimal("1000"),
        COMMISSION_USD=Decimal("0"),
        MIN_ORDER_USD=Decimal("5"),
        SLIPPAGE_BPS=5,
        FILL_TIMEOUT_MINUTES=30,
        DATA_DELAY_MINUTES=0,
    )


def _universe() -> Universe:
    listings = (
        Listing("AAPL", "Apple", "Technology", None),
        Listing("MSFT", "Microsoft", "Technology", None),
        Listing("SPY", "SPDR", "ETF", None),
    )
    sectors: dict[str, list[Listing]] = {}
    for item in listings:
        sectors.setdefault(item.sector, []).append(item)
    return Universe(
        listings,
        MappingProxyType({item.ticker: item for item in listings}),
        MappingProxyType({sector: tuple(items) for sector, items in sectors.items()}),
    )


def _calendar() -> SessionCalendar:
    return SessionCalendar(((_OPENED, _CLOSED),))


def _price(when: datetime) -> Decimal:
    if when.astimezone(_ET) >= _DROP:
        return Decimal("80")
    return Decimal("100")


def _bars(tickers: tuple[str, ...] = _NAMES) -> list[BarInput]:
    bars: list[BarInput] = []
    moment = _OPENED
    while moment < _CLOSED:
        price = _price(moment)
        for ticker in tickers:
            bars.append(
                BarInput(
                    ticker=ticker,
                    t=moment,
                    o=price,
                    h=price,
                    l=price,
                    c=price,
                    v=Decimal("100"),
                    feed="test",
                )
            )
        moment += timedelta(minutes=1)
    return bars


def _run(engine, body) -> None:
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )()
    try:
        body(session)
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(scope="module")
def replay_engine():
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    before = _user_count(engine)
    yield engine
    after = _user_count(engine)
    engine.dispose()
    assert after == before


def test_replay_source_does_not_call_a_model_or_the_provider() -> None:
    text = _REPLAY.read_text()
    for banned in ("openai", "langgraph", "langchain", "massive_data", "httpx", "requests"):
        assert banned not in text


def test_latest_known_price_waits_until_the_minute_finishes(replay_engine) -> None:
    def body(session: Session) -> None:
        ten = datetime(2026, 6, 2, 10, 0, tzinfo=_ET)
        earlier = ten - timedelta(minutes=1)
        assert upsert_bars(
            session,
            [
                BarInput(
                    ticker="AAPL",
                    t=earlier,
                    o=Decimal("10"),
                    h=Decimal("10"),
                    l=Decimal("10"),
                    c=Decimal("10"),
                    v=Decimal("1"),
                    feed="test",
                ),
                BarInput(
                    ticker="AAPL",
                    t=ten,
                    o=Decimal("12"),
                    h=Decimal("12.34"),
                    l=Decimal("12"),
                    c=Decimal("12.34"),
                    v=Decimal("1"),
                    feed="test",
                ),
                BarInput(
                    ticker="MSFT",
                    t=ten,
                    o=Decimal("12"),
                    h=Decimal("12.34"),
                    l=Decimal("12"),
                    c=Decimal("12.34"),
                    v=Decimal("1"),
                    feed="test",
                ),
            ],
        ) == 3
        at_ten = latest_known_prices(session, ["AAPL", "MSFT"], ten)
        assert at_ten["AAPL"] == Decimal("10")
        assert "MSFT" not in at_ten
        at_next = latest_known_prices(session, ["AAPL", "MSFT"], ten + timedelta(minutes=1))
        assert at_next["AAPL"] == Decimal("12.34")
        assert at_next["MSFT"] == Decimal("12.34")
        assert latest_known_prices(session, [], ten) == {}

    _run(replay_engine, body)


def test_closed_day_is_named_and_a_real_session_has_bounds() -> None:
    calendar = XNYSCalendar()
    with pytest.raises(ReplayRefused, match="2026-11-26 is not an XNYS session"):
        require_session(calendar, date(2026, 11, 26))
    opened, closed = require_session(calendar, date(2026, 11, 24))
    assert opened.astimezone(_ET) == datetime(2026, 11, 24, 9, 30, tzinfo=_ET)
    assert closed.astimezone(_ET) == datetime(2026, 11, 24, 16, 0, tzinfo=_ET)


def test_replay_refuses_a_ticker_with_no_bar_and_does_not_invent_one(replay_engine) -> None:
    def body(session: Session) -> None:
        assert upsert_bars(session, _bars(("MSFT", "SPY"))) == 390 * 2
        with pytest.raises(ReplayRefused, match="AAPL") as caught:
            replay_day(
                session,
                opened=_OPENED,
                closed=_CLOSED,
                universe=_universe(),
                risk_level="balanced",
                seed=42,
                settings=_settings(),
                calendar=_calendar(),
                starting_cash=Decimal("1000"),
            )
        message = str(caught.value)
        assert "MSFT" not in message
        assert "backfill_bars.py" in message
        assert "does not fetch" in message

    _run(replay_engine, body)


def test_same_seed_replays_match_and_hold_invariants(replay_engine) -> None:
    found = []

    def body(session: Session) -> None:
        assert upsert_bars(session, _bars()) == 390 * 3
        summary = replay_day(
            session,
            opened=_OPENED,
            closed=_CLOSED,
            universe=_universe(),
            risk_level="balanced",
            seed=42,
            settings=_settings(),
            calendar=_calendar(),
            starting_cash=Decimal("1000"),
        )
        found.append(summary)

    _run(replay_engine, body)
    _run(replay_engine, body)
    first, second = found
    assert first == second
    assert first.invariants == "pass"
    assert first.player_orders == 2
    assert first.fills > 0
    assert first.actual_cash != first.ghost_cash
    assert first.spy_return == Decimal("-0.20000000")
    assert first.actual_cash >= 0
    assert first.ghost_cash >= 0


def test_unknown_risk_level_and_non_positive_cash_are_refused(replay_engine) -> None:
    def body(session: Session) -> None:
        shared = dict(
            opened=_OPENED,
            closed=_CLOSED,
            universe=_universe(),
            seed=42,
            settings=_settings(),
            calendar=_calendar(),
        )
        with pytest.raises(ReplayRefused, match="unknown risk level"):
            replay_day(session, risk_level="yolo", starting_cash=Decimal("1000"), **shared)
        with pytest.raises(ReplayRefused, match="starting cash"):
            replay_day(session, risk_level="balanced", starting_cash=Decimal("0"), **shared)

    _run(replay_engine, body)

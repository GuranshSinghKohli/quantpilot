"""Massive minute wrapper. The default suite does not open a socket."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from alembic import command
from sqlalchemy.orm import sessionmaker

from app.league.market.massive_data import (
    MassiveResponseError,
    fetch_minute_bars,
    resolve_tickers,
    run_backfill,
)
from app.league.repo.bars import first_bar_after
from app.league.sim.calendar import CalendarUnavailable, SessionCalendar
from app.league.sim.xnys import XNYSCalendar
from app.pipeline.retry import RetryPolicy
from app.pipeline.sources.http import SourceClient, Throttle
from tests.unit.league.test_league_migration import _config, _engine, _url

_OPEN = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
_CLOSE = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)
_BASE = "https://api.massive.com"


class FakeClient:
    def __init__(self, bodies: list[dict]) -> None:
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, target: str, params: dict | None = None, *, operation: str):
        self.calls.append((target, params))
        if not self.bodies:
            raise AssertionError(f"unexpected call {target}")
        return self.bodies.pop(0), {}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _millis(moment: datetime) -> int:
    return int(moment.timestamp()) * 1000


def _raw(moment: datetime, *, volume: object = 10.5, price: object = 10.25) -> dict:
    return {"o": price, "h": price, "l": price, "c": price, "v": volume, "t": _millis(moment)}


def _page(bars: list[dict], nxt: str | None = None, *, adjusted: object = False) -> dict:
    body: dict = {"status": "OK", "ticker": "AAPL", "adjusted": adjusted, "results": bars}
    if nxt is not None:
        body["next_url"] = nxt
    return body


def _session() -> SessionCalendar:
    return SessionCalendar([(_OPEN, _CLOSE)])


@pytest.fixture
def bars_session():
    url = _url()
    engine = _engine(url)
    command.upgrade(_config(url), "head")
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
        engine.dispose()


def test_timestamp_is_the_start_of_the_minute_in_utc() -> None:
    later = _OPEN + timedelta(minutes=1)
    client = FakeClient([_page([_raw(_OPEN), _raw(later, volume="1.23456789")])])
    result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    assert result.calls == 1
    assert [bar.t for bar in result.bars] == [_OPEN, later]
    assert result.bars[0].t != _OPEN + timedelta(minutes=1)
    assert result.bars[0].v == Decimal("10.5")
    assert result.bars[1].v == Decimal("1.234568")
    assert result.bars[0].feed == "massive"
    assert client.calls[0][0].endswith("/range/1/minute/2026-11-24/2026-11-24")
    assert client.calls[0][1] == {"adjusted": "false", "sort": "asc", "limit": 50_000}


def test_close_instant_and_premarket_are_dropped() -> None:
    premarket = _OPEN - timedelta(hours=1)
    client = FakeClient([_page([_raw(premarket), _raw(_OPEN), _raw(_CLOSE)])])
    result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    assert [bar.t for bar in result.bars] == [_OPEN]
    assert result.skipped["outside_session"] == 2


def test_early_close_keeps_1259_and_drops_1300() -> None:
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    kept = datetime(2026, 11, 27, 12, 59, tzinfo=ny)
    close = datetime(2026, 11, 27, 13, 0, tzinfo=ny)
    after = datetime(2026, 11, 27, 13, 5, tzinfo=ny)
    client = FakeClient([_page([_raw(kept), _raw(close), _raw(after)], adjusted=False)])
    result = fetch_minute_bars(
        client, "AAPL", date(2026, 11, 27), date(2026, 11, 27), XNYSCalendar(), base_url=_BASE
    )
    assert [bar.t for bar in result.bars] == [kept.astimezone(timezone.utc)]
    assert result.skipped["outside_session"] == 2


def test_non_positive_price_is_skipped_and_a_good_bar_is_kept() -> None:
    client = FakeClient([_page([_raw(_OPEN, price=0), _raw(_OPEN + timedelta(minutes=1))])])
    result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    assert len(result.bars) == 1
    assert result.skipped["non_positive_price"] == 1


def test_next_url_on_another_host_is_not_requested(caplog) -> None:
    secret = "should-not-be-sent"
    foreign = f"https://evil.example/steal?apiKey={secret}"
    client = FakeClient([
        _page([_raw(_OPEN)], nxt=foreign),
        _page([_raw(_OPEN + timedelta(minutes=1))]),
    ])
    caplog.set_level(logging.WARNING, logger="app.league.market.massive_data")
    result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    assert len(client.calls) == 1
    assert "evil.example" not in client.calls[0][0]
    assert [bar.t for bar in result.bars] == [_OPEN]
    assert result.skipped["next_url_other_host"] == 1
    assert "evil.example" in caplog.text
    assert secret not in caplog.text


def test_next_url_on_the_same_host_is_followed() -> None:
    nxt = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/2026-11-24/2026-11-24?cursor=2"
    second = _OPEN + timedelta(minutes=1)
    client = FakeClient([_page([_raw(_OPEN)], nxt=nxt), _page([_raw(second)])])
    result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    assert [bar.t for bar in result.bars] == [_OPEN, second]
    assert client.calls[1] == (nxt, None)


def test_adjusted_prices_are_refused() -> None:
    client = FakeClient([_page([_raw(_OPEN)], adjusted=True)])
    with pytest.raises(MassiveResponseError, match="adjusted"):
        fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)


def test_bar_outside_the_loaded_range_is_not_guessed() -> None:
    far = datetime(2099, 6, 15, 14, 30, tzinfo=timezone.utc)
    client = FakeClient([_page([_raw(far)])])
    with pytest.raises(CalendarUnavailable):
        fetch_minute_bars(client, "AAPL", date(2099, 6, 15), date(2099, 6, 15), XNYSCalendar(), base_url=_BASE)


def test_pagination_is_spaced_at_five_per_minute() -> None:
    nxt = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/2026-11-24/2026-11-24?cursor=2"
    pages = [
        _page([_raw(_OPEN)], nxt=nxt),
        _page([_raw(_OPEN + timedelta(minutes=1))]),
    ]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=pages.pop(0))

    clock = _Clock()
    client = _http_client(httpx.MockTransport(handler), clock)
    try:
        result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    finally:
        client.close()
    assert len(result.bars) == 2
    assert clock.sleeps == [12.0]
    assert seen[1].startswith("https://api.massive.com/")
    assert "test-key-not-real" not in seen[1]


def test_429_is_retried_with_backoff() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=_page([_raw(_OPEN)]))

    clock = _Clock()
    client = _http_client(
        httpx.MockTransport(handler),
        clock,
        policy=RetryPolicy(max_attempts=3, base_delay=0.25, max_delay=30, jitter=False),
    )
    try:
        result = fetch_minute_bars(client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE)
    finally:
        client.close()
    assert calls["n"] == 2
    assert len(result.bars) == 1
    assert clock.sleeps == [0.25, 11.75]


def test_fixture_universe_has_26_tickers() -> None:
    tickers = resolve_tickers(universe_path="backend/tests/fixtures/universe_small.csv")
    assert len(tickers) == 26
    assert "BRK.B" in tickers
    assert "AAPL" in tickers


def test_resolve_tickers_rejects_a_bad_symbol_and_drops_duplicates() -> None:
    with pytest.raises(ValueError, match="brk.b"):
        resolve_tickers(["brk.b"])
    assert resolve_tickers(["AAPL", "AAPL", "MSFT"]) == ("AAPL", "MSFT")


def test_backfill_is_idempotent_and_stores_fractional_volume(bars_session) -> None:
    outside = _CLOSE
    bodies = [_page([_raw(_OPEN, volume=10.5), _raw(outside, volume=3.25)])]
    first = run_backfill(
        bars_session, FakeClient(list(bodies)), ["AAPL"], date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE
    )
    second = run_backfill(
        bars_session, FakeClient(list(bodies)), ["AAPL"], date(2026, 11, 24), date(2026, 11, 24), _session(), base_url=_BASE
    )
    assert first.inserted == 1
    assert first.skipped["outside_session"] == 1
    assert second.inserted == 0
    assert second.skipped["already_present"] == 1
    stored = first_bar_after(bars_session, "AAPL", _OPEN - timedelta(minutes=1), _OPEN)
    assert stored is not None
    assert stored.v == Decimal("10.5")
    assert stored.feed == "massive"
    assert stored.t == _OPEN


@pytest.mark.skipif(os.environ.get("LEAGUE_MASSIVE_SMOKE") != "1", reason="set LEAGUE_MASSIVE_SMOKE=1 to call Massive")
def test_live_smoke_one_aapl_session() -> None:
    from dotenv import dotenv_values
    from pydantic import SecretStr

    from app.core.settings import BACKEND_DIR, Settings
    from app.league.market.massive_data import build_massive_client

    values = dotenv_values(BACKEND_DIR / ".env")
    key = values.get("MARKET_API_KEY") or ""
    if not key:
        pytest.skip("MARKET_API_KEY is not set")
    settings = Settings(
        MARKET_API_KEY=SecretStr(key),
        MARKET_BASE_URL=values.get("MARKET_BASE_URL") or _BASE,
        MARKET_REQUESTS_PER_MINUTE=5,
    )
    client = build_massive_client(settings)
    try:
        result = fetch_minute_bars(
            client, "AAPL", date(2026, 11, 24), date(2026, 11, 24), XNYSCalendar(), base_url=settings.MARKET_BASE_URL
        )
    finally:
        client.close()
    assert result.calls >= 1
    assert result.bars
    assert result.bars[0].t.tzinfo is not None
    assert all(bar.feed == "massive" for bar in result.bars)


def _http_client(transport: httpx.BaseTransport, clock: _Clock, policy: RetryPolicy | None = None) -> SourceClient:
    return SourceClient(
        provider="massive",
        base_url=_BASE,
        headers={"Authorization": "Bearer test-key-not-real", "Accept": "application/json"},
        timeout_s=5,
        throttle=Throttle(5, clock=clock, sleep=clock.sleep),
        policy=policy or RetryPolicy(max_attempts=1, base_delay=0.25, max_delay=30, jitter=False),
        max_retry_after_s=120,
        transport=transport,
        sleep=clock.sleep,
    )

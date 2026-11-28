"""Trading-session windows from fixture bars. No network."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.market.calendar import (
    SessionBar,
    TradingCalendar,
    daily_returns,
    event_calendar_date,
    event_timestamp,
    sample_stdev,
    simple_return,
)


def _bar(day: date, close: str, volume: int = 1000) -> SessionBar:
    price = Decimal(close)
    return SessionBar(trading_date=day, open=price, high=price, low=price, close=price, volume=volume)


def _cal() -> TradingCalendar:
    # Fri 16 Jan 2026 is T0. 19 Jan is a holiday (no bar). Weekends omitted.
    days = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 13),
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 1, 16),
        date(2026, 1, 20),
        date(2026, 1, 21),
        date(2026, 1, 22),
        date(2026, 1, 23),
        date(2026, 1, 26),
    ]
    return TradingCalendar([_bar(day, "100") for day in days])


def test_event_on_trading_day_is_t0():
    cal = _cal()
    t0 = cal.event_trading_date(datetime(2026, 1, 16, 14, 30, tzinfo=timezone.utc))
    assert t0 == date(2026, 1, 16)


def test_weekend_maps_to_next_session():
    cal = _cal()
    assert cal.event_trading_date(datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc)) == date(2026, 1, 20)
    assert cal.event_trading_date(datetime(2026, 1, 18, 12, 0, tzinfo=timezone.utc)) == date(2026, 1, 20)


def test_holiday_maps_to_next_session():
    cal = _cal()
    assert cal.event_trading_date(datetime(2026, 1, 19, 16, 0, tzinfo=timezone.utc)) == date(2026, 1, 20)


def test_before_and_after_close_share_utc_date():
    cal = _cal()
    before = datetime(2026, 1, 16, 9, 0, tzinfo=timezone.utc)
    after = datetime(2026, 1, 16, 22, 0, tzinfo=timezone.utc)
    assert cal.event_trading_date(before) == cal.event_trading_date(after) == date(2026, 1, 16)


def test_session_offsets():
    cal = _cal()
    t0 = date(2026, 1, 16)
    assert cal.offset(t0, -1).trading_date == date(2026, 1, 15)
    assert cal.offset(t0, 1).trading_date == date(2026, 1, 20)
    assert cal.offset(t0, 3).trading_date == date(2026, 1, 22)
    assert cal.offset(t0, 5).trading_date == date(2026, 1, 26)
    assert cal.offset(t0, 20) is None


def test_missing_future_and_baseline_are_none():
    cal = TradingCalendar([_bar(date(2026, 1, 16), "100")])
    assert cal.offset(date(2026, 1, 16), 1) is None
    assert cal.event_trading_date(datetime(2026, 1, 2, tzinfo=timezone.utc)) == date(2026, 1, 16)
    empty = TradingCalendar([])
    assert empty.event_trading_date(datetime(2026, 1, 16, tzinfo=timezone.utc)) is None


def test_invalid_bars_are_dropped():
    cal = TradingCalendar(
        [
            _bar(date(2026, 1, 15), "100"),
            SessionBar(date(2026, 1, 16), Decimal("10"), Decimal("9"), Decimal("11"), Decimal("10"), 1),
            SessionBar(date(2026, 1, 20), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), -5),
        ]
    )
    assert cal.dates() == [date(2026, 1, 15)]


def test_returns_and_stdev_are_exact():
    assert simple_return(Decimal("100"), Decimal("110")) == pytest.approx(0.1)
    assert simple_return(Decimal("0"), Decimal("10")) is None
    assert simple_return(Decimal("-1"), Decimal("10")) is None
    assert simple_return(None, Decimal("10")) is None
    returns = daily_returns([Decimal("100"), Decimal("110"), Decimal("121")])
    assert returns == pytest.approx([0.1, 0.1])
    assert sample_stdev([0.0, 0.0]) == 0.0
    assert abs(sample_stdev([1.0, 0.5]) - (0.125 ** 0.5)) < 1e-12
    assert sample_stdev([1.0]) is None


def test_publication_is_not_occurrence():
    occurred = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    published = datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)
    assert event_timestamp(occurred_at=occurred, published_at=published) == occurred
    assert event_timestamp(occurred_at=None, published_at=published) == published
    assert event_calendar_date(published) == date(2026, 1, 16)

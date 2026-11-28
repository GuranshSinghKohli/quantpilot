"""XNYS calendar against known sessions. No network and no wall clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest

from app.league.sim.calendar import CalendarUnavailable, SessionCalendar
from app.league.sim.xnys import XNYSCalendar

_NY = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=_NY)


def test_early_close_ends_at_1300_et() -> None:
    calendar = XNYSCalendar()
    assert calendar.is_open(_et(2026, 11, 27, 9, 30))
    assert calendar.is_open(_et(2026, 11, 27, 12, 59))
    assert not calendar.is_open(_et(2026, 11, 27, 13, 0))
    assert not calendar.is_open(_et(2026, 11, 27, 13, 5))


def test_thanksgiving_is_closed_and_the_next_open_is_the_early_close_day() -> None:
    calendar = XNYSCalendar()
    morning = _et(2026, 11, 26, 10, 0)
    assert not calendar.is_open(morning)
    assert calendar.next_open(morning) == _et(2026, 11, 27, 9, 30)


def test_normal_session_is_0930_to_1600_et() -> None:
    calendar = XNYSCalendar()
    assert calendar.is_open(_et(2026, 11, 24, 9, 30))
    assert calendar.is_open(_et(2026, 11, 24, 15, 59))
    assert not calendar.is_open(_et(2026, 11, 24, 16, 0))
    assert calendar.next_open(_et(2026, 11, 24, 8, 0)) == _et(2026, 11, 24, 9, 30)


def test_weekend_inside_the_loaded_range_is_closed() -> None:
    calendar = XNYSCalendar()
    saturday = _et(2026, 11, 28, 10, 0)
    assert not calendar.is_open(saturday)
    assert calendar.next_open(saturday) == _et(2026, 11, 30, 9, 30)


def test_early_close_stops_the_trading_minute_count() -> None:
    calendar = XNYSCalendar()
    start = _et(2026, 11, 27, 12, 0)
    end = _et(2026, 11, 27, 14, 0)
    scripted = SessionCalendar([(_et(2026, 11, 27, 9, 30), _et(2026, 11, 27, 13, 0))])
    assert calendar.trading_minutes(start, end) == 60
    assert calendar.trading_minutes(start, end) == scripted.trading_minutes(start, end)
    overnight = calendar.trading_minutes(_et(2026, 11, 26, 10, 0), _et(2026, 11, 27, 10, 0))
    assert overnight == 30


def test_no_session_is_invented_after_the_last_loaded_close() -> None:
    bound = xcals.get_calendar("XNYS")
    close = bound.session_close(bound.last_session).to_pydatetime()
    after = close + timedelta(hours=1)
    calendar = XNYSCalendar()
    assert calendar.is_open(after) is False
    assert calendar.next_open(after) is None


def test_date_outside_the_loaded_range_raises() -> None:
    bound = xcals.get_calendar("XNYS")
    far = datetime(2099, 6, 15, 10, 0, tzinfo=_NY)
    assert far.date() > bound.last_session.date()
    calendar = XNYSCalendar()
    with pytest.raises(CalendarUnavailable) as is_open:
        calendar.is_open(far)
    assert is_open.value.moment == far.astimezone(timezone.utc)
    with pytest.raises(CalendarUnavailable):
        calendar.next_open(far)
    with pytest.raises(CalendarUnavailable):
        calendar.trading_minutes(far, far + timedelta(hours=1))
    before = datetime(1990, 1, 2, 10, 0, tzinfo=_NY)
    assert before.date() < bound.first_session.date()
    with pytest.raises(CalendarUnavailable):
        calendar.is_open(before)

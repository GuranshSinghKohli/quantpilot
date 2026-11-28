"""XNYS session calendar. Offline. Times are UTC.

The loaded range is whatever ``exchange_calendars`` ships for XNYS. A date
outside that range raises CalendarUnavailable. This module does not invent a
later or earlier session.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from app.league.sim.calendar import CalendarUnavailable
from app.league.sim.clock import _as_utc

_NY = ZoneInfo("America/New_York")
_CALENDAR = xcals.get_calendar("XNYS")


def _instant(value) -> datetime:
    """exchange_calendars session bounds are UTC. A naive value is that contract."""
    moment = value.to_pydatetime()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


class XNYSCalendar:
    """NYSE regular hours, holidays, and early closes. The close instant is not open."""

    def __init__(self) -> None:
        self._cal = _CALENDAR
        self._first: date = self._cal.first_session.date()
        self._last: date = self._cal.last_session.date()

    def is_open(self, t: datetime) -> bool:
        moment = _as_utc(t)
        self._require_known_day(moment)
        bounds = self._bounds(moment)
        if bounds is None:
            return False
        opened, closed = bounds
        return opened <= moment < closed

    def next_open(self, t: datetime) -> datetime | None:
        """The next session open at or after t, when t itself is closed.

        When t is already inside a session, this is the following session's
        open, matching SessionCalendar. None means the loaded range has no
        later open. It is not a guess past the last loaded session.
        """
        moment = _as_utc(t)
        self._require_known_day(moment)
        day = moment.astimezone(_NY).date()
        for session in self._cal.sessions_in_range(day, self._last):
            opened = _instant(self._cal.session_open(session))
            if opened > moment or (opened == moment and not self.is_open(moment)):
                return opened
        return None

    def trading_minutes(self, start: datetime, end: datetime) -> int:
        """Open-market minutes in (start, end]. Time outside a session is zero."""
        opened = _as_utc(start)
        closed = _as_utc(end)
        self._require_known_day(opened)
        self._require_known_day(closed)
        if closed <= opened:
            return 0
        seconds = 0.0
        sessions = self._cal.sessions_in_range(
            opened.astimezone(_NY).date(),
            closed.astimezone(_NY).date(),
        )
        for session in sessions:
            left = max(opened, _instant(self._cal.session_open(session)))
            right = min(closed, _instant(self._cal.session_close(session)))
            if right > left:
                seconds += (right - left).total_seconds()
        return int(seconds // 60)

    def _require_known_day(self, moment: datetime) -> None:
        day = moment.astimezone(_NY).date()
        if day < self._first or day > self._last:
            raise CalendarUnavailable(moment)

    def _bounds(self, moment: datetime) -> tuple[datetime, datetime] | None:
        day = moment.astimezone(_NY).date()
        if not self._cal.is_session(day):
            return None
        return _instant(self._cal.session_open(day)), _instant(self._cal.session_close(day))

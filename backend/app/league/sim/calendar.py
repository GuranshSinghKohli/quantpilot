"""Session calendar for tests. The XNYS calendar is ``app.league.sim.xnys``. Times are UTC."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.league.sim.clock import NaiveClockTime, _as_utc


class MarketCalendar(Protocol):
    def is_open(self, t: datetime) -> bool:
        """True when t is inside a session. The close instant is not open."""

    def next_open(self, t: datetime) -> datetime | None:
        """The next session open at or after t, when t itself is closed."""

    def trading_minutes(self, start: datetime, end: datetime) -> int:
        """Open-market minutes in (start, end]. Time outside a session is zero."""


class CalendarUnavailable(Exception):
    """The market is closed and the calendar has no following open."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment
        super().__init__(f"no next open at or after {moment.isoformat()}")


class SessionCalendar:
    """Explicit [open, close) sessions. Sessions must not overlap."""

    def __init__(self, sessions: Sequence[tuple[datetime, datetime]]) -> None:
        ordered: list[tuple[datetime, datetime]] = []
        for open_t, close_t in sessions:
            start = _as_utc(open_t)
            end = _as_utc(close_t)
            if start >= end:
                raise ValueError(f"session open {start.isoformat()} is not before close")
            ordered.append((start, end))
        ordered.sort()
        for index, (start, end) in enumerate(ordered):
            if index and start < ordered[index - 1][1]:
                raise ValueError("sessions overlap")
        self._sessions = tuple(ordered)

    def is_open(self, t: datetime) -> bool:
        moment = _as_utc(t)
        return any(start <= moment < end for start, end in self._sessions)

    def next_open(self, t: datetime) -> datetime | None:
        moment = _as_utc(t)
        for start, _end in self._sessions:
            if start > moment or (start == moment and not self.is_open(moment)):
                return start
        return None

    def trading_minutes(self, start: datetime, end: datetime) -> int:
        opened = _as_utc(start)
        closed = _as_utc(end)
        if closed <= opened:
            return 0
        seconds = 0.0
        for session_open, session_close in self._sessions:
            left = max(opened, session_open)
            right = min(closed, session_close)
            if right > left:
                seconds += (right - left).total_seconds()
        return int(seconds // 60)

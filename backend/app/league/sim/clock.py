"""Game clock. UTC instants only. Time does not move backwards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from app.league.config import LeagueSettings


class Clock(Protocol):
    def now(self) -> datetime:
        """Current game time, timezone-aware UTC."""


class ClockError(Exception):
    """A clock was given a time it cannot use."""


class NaiveClockTime(ClockError):
    """A datetime without a timezone."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment
        super().__init__(f"naive datetime is not allowed: {moment!r}")


class ClockWentBackwards(ClockError):
    """set() or advance() would move the clock to an earlier instant."""

    def __init__(self, current: datetime, requested: datetime) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"clock cannot move from {current.isoformat()} to {requested.isoformat()}")


class InvalidDelay(ClockError):
    """DATA_DELAY_MINUTES is not a non-negative integer."""

    def __init__(self, delay_minutes: object) -> None:
        self.delay_minutes = delay_minutes
        super().__init__(f"delay minutes must be a non-negative integer: {delay_minutes!r}")


def _wall_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise NaiveClockTime(moment)
    return moment.astimezone(timezone.utc)


class SystemClock:
    """Wall time minus the data delay. Tests inject utc_now."""

    def __init__(self, delay_minutes: int, *, utc_now: Callable[[], datetime] | None = None) -> None:
        if isinstance(delay_minutes, bool) or not isinstance(delay_minutes, int) or delay_minutes < 0:
            raise InvalidDelay(delay_minutes)
        self._delay = timedelta(minutes=delay_minutes)
        self._utc_now = utc_now or _wall_utc

    def now(self) -> datetime:
        return _as_utc(self._utc_now()) - self._delay


class ManualClock:
    """A test clock. set() moves only forward. advance() takes a non-negative timedelta."""

    def __init__(self, moment: datetime) -> None:
        self._moment = _as_utc(moment)

    def now(self) -> datetime:
        return self._moment

    def set(self, moment: datetime) -> None:
        utc = _as_utc(moment)
        if utc < self._moment:
            raise ClockWentBackwards(self._moment, utc)
        self._moment = utc

    def advance(self, delta: timedelta) -> None:
        if not isinstance(delta, timedelta):
            raise TypeError(f"advance() requires a timedelta, got {type(delta).__name__}")
        requested = self._moment + delta
        if delta < timedelta(0):
            raise ClockWentBackwards(self._moment, requested)
        self._moment = requested


FixedClock = ManualClock


def system_clock(
    settings: LeagueSettings | None = None,
    *,
    utc_now: Callable[[], datetime] | None = None,
) -> SystemClock:
    """Build the game clock from section 15. The delay is DATA_DELAY_MINUTES."""
    resolved = LeagueSettings() if settings is None else settings
    return SystemClock(resolved.DATA_DELAY_MINUTES, utc_now=utc_now)

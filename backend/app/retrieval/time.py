"""UTC bounds for retrieval. One policy for documents and events.

Publication filters use ``published_at``. Event filters use ``occurred_at``.
``retrieved_at`` and ingestion time are not filter columns.

Windows are half-open: ``start <= timestamp < end``, both UTC.
A date-only end covers that whole civil day: ``[day 00:00Z, next day 00:00Z)``.

Naive values are rejected at the API boundary. SQLite reads drop offsets, so
values loaded from SQLite are treated as UTC only inside ``as_stored_utc``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional


class NaiveTimestamp(ValueError):
    """A caller passed a datetime with no offset."""


def as_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive input is rejected."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveTimestamp("timestamp needs a UTC offset")
    return value.astimezone(timezone.utc)


def as_stored_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize a value read from the database. SQLite returns naive UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def day_window(start: date, end: date) -> tuple[datetime, datetime]:
    """Inclusive civil dates in UTC as ``[start 00:00Z, day after end 00:00Z)``."""
    if end < start:
        raise ValueError("end date is before start date")
    return (
        datetime.combine(start, time(0), timezone.utc),
        datetime.combine(end + timedelta(days=1), time(0), timezone.utc),
    )


def window(start: Optional[datetime], end: Optional[datetime]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Normalize an instant window. ``end`` is exclusive."""
    return (
        as_utc(start) if start is not None else None,
        as_utc(end) if end is not None else None,
    )

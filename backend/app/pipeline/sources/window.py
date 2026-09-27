"""Ingestion windows: explicit, bounded, UTC."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from app.pipeline.sources.registry import ConfigurationError


def parse_bound(value: Optional[str], *, end: bool) -> Optional[datetime]:
    """``YYYY-MM-DD`` (start of day; for ``end``, the last second of that day) or an
    ISO 8601 instant with an offset. Naive instants are rejected, not assumed UTC."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime.combine(day, time(23, 59, 59) if end else time(0), timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"not a date or ISO 8601 instant: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"instant needs a UTC offset (e.g. Z): {value!r}")
    return parsed.astimezone(timezone.utc)


def resolve_window(
    start: Optional[str],
    end: Optional[str],
    *,
    lookback_days: int,
    max_window_days: int,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    end_at = parse_bound(end, end=True) or now
    start_at = parse_bound(start, end=False) or end_at - timedelta(days=lookback_days)
    if start_at >= end_at:
        raise ConfigurationError(f"window start {start_at.isoformat()} is not before end {end_at.isoformat()}")
    if end_at > now + timedelta(days=1):
        raise ConfigurationError(f"window end {end_at.isoformat()} is in the future")
    if end_at - start_at > timedelta(days=max_window_days):
        raise ConfigurationError(
            f"window of {(end_at - start_at).days} days exceeds INGEST_MAX_WINDOW_DAYS={max_window_days}"
        )
    return start_at, end_at

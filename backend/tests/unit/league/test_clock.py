"""Game clock: delay, forward-only manual time, and one wall-clock call."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.league.config import LeagueSettings
from app.league.sim.clock import (
    ClockWentBackwards,
    FixedClock,
    InvalidDelay,
    ManualClock,
    NaiveClockTime,
    SystemClock,
    system_clock,
)

_UTC = timezone.utc
_START = datetime(2026, 1, 2, 15, 0, tzinfo=_UTC)
_LEAGUE = Path(__file__).resolve().parents[3] / "app" / "league"
_FORBIDDEN = ("datetime.utcnow", "datetime.today", "date.today", "time.time")


def test_system_clock_subtracts_the_injected_delay() -> None:
    moment = _START
    assert SystemClock(0, utc_now=lambda: moment).now() == moment
    assert SystemClock(15, utc_now=lambda: moment).now() == moment - timedelta(minutes=15)
    new_york = datetime(2026, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    clock = SystemClock(0, utc_now=lambda: new_york)
    assert clock.now() == new_york.astimezone(_UTC)
    assert clock.now().tzinfo == _UTC


def test_system_clock_uses_data_delay_minutes() -> None:
    settings = LeagueSettings(DATA_DELAY_MINUTES=15)
    clock = system_clock(settings, utc_now=lambda: _START)
    assert clock.now() == _START - timedelta(minutes=15)


def test_negative_and_non_integer_delays_are_rejected() -> None:
    with pytest.raises(InvalidDelay):
        SystemClock(-1, utc_now=lambda: _START)
    with pytest.raises(InvalidDelay):
        SystemClock(True, utc_now=lambda: _START)  # type: ignore[arg-type]


def test_naive_datetimes_raise_and_are_not_stored() -> None:
    naive = datetime(2026, 1, 2, 15, 0)
    with pytest.raises(NaiveClockTime):
        ManualClock(naive)
    clock = ManualClock(_START)
    with pytest.raises(NaiveClockTime):
        clock.set(naive)
    assert clock.now() == _START
    with pytest.raises(NaiveClockTime):
        SystemClock(0, utc_now=lambda: naive).now()


def test_manual_clock_moves_only_forward() -> None:
    clock = ManualClock(_START)
    clock.set(_START)
    assert clock.now() == _START
    later = _START + timedelta(minutes=5)
    clock.set(later)
    clock.advance(timedelta(0))
    clock.advance(timedelta(minutes=1))
    assert clock.now() == later + timedelta(minutes=1)
    earlier = clock.now() - timedelta(seconds=1)
    with pytest.raises(ClockWentBackwards):
        clock.set(earlier)
    assert clock.now() == later + timedelta(minutes=1)
    with pytest.raises(ClockWentBackwards):
        clock.advance(timedelta(seconds=-1))
    assert clock.now() == later + timedelta(minutes=1)
    new_york_later = datetime(2026, 1, 2, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    clock.set(new_york_later)
    assert clock.now() == new_york_later.astimezone(_UTC)


def test_fixed_clock_is_the_manual_clock() -> None:
    assert FixedClock is ManualClock


def test_league_source_has_one_wall_clock_call() -> None:
    now_hits: list[str] = []
    forbidden: list[str] = []
    for path in sorted(_LEAGUE.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            relative = path.relative_to(_LEAGUE).as_posix()
            if any(token in line for token in _FORBIDDEN):
                forbidden.append(f"{relative}:{number}: {line.strip()}")
            if "datetime.now" in line:
                now_hits.append(f"{relative}:{number}: {line.strip()}")
    assert forbidden == []
    assert len(now_hits) == 1
    assert now_hits[0].startswith("sim/clock.py:")
    assert now_hits[0].endswith("return datetime.now(timezone.utc)")

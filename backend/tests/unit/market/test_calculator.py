"""Hand-computable event-impact calculations and idempotency."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.enums import EventStatus
from app.domain.models import EventImpact
from app.market import CALCULATION_VERSION
from app.market.calendar import daily_returns, sample_stdev
from app.market.calculator import EventImpactCalculator
from tests.unit.market.helpers import (
    NOW,
    close_db,
    company,
    confirmed_event,
    migrated_db,
    seed_sessions,
    weekdays,
)

T0 = date(2026, 2, 2)
PRIOR = weekdays(date(2026, 1, 5), 20)
FUTURE = weekdays(date(2026, 2, 3), 20)
assert PRIOR[-1] == date(2026, 1, 30)
assert FUTURE[0] == date(2026, 2, 3)


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def _closes():
    future_close = {
        FUTURE[0]: "110",  # +1
        FUTURE[1]: "120",  # +2
        FUTURE[2]: "125",  # +3
        FUTURE[3]: "140",  # +4
        FUTURE[4]: "150",  # +5
        FUTURE[19]: "200",  # +20
    }
    rows = [(day, "80", 1000) for day in PRIOR]
    rows.append((T0, "100", 2500))
    for i, day in enumerate(FUTURE):
        rows.append((day, future_close.get(day, "130"), 1000))
    return rows


def _bench():
    future_close = {
        FUTURE[0]: "220",
        FUTURE[2]: "240",
        FUTURE[4]: "200",
        FUTURE[19]: "180",
    }
    rows = [(day, "200", 1000) for day in PRIOR]
    rows.append((T0, "200", 1000))
    for day in FUTURE:
        rows.append((day, future_close.get(day, "210"), 1000))
    return rows


def test_windows_benchmark_volume_and_volatility(db, monkeypatch):
    monkeypatch.setenv("MARKET_BENCHMARK_SYMBOL", "SPY")
    from app.core.settings import get_settings

    get_settings.cache_clear()
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc))
    seed_sessions(db, "NVDA", _closes())
    seed_sessions(db, "SPY", _bench(), start_n=500)
    report = EventImpactCalculator(benchmark_symbol="SPY").calculate_event(db, event.id)
    assert report["result"] in {"ok", "partial"}
    row = db.query(EventImpact).one()
    assert row.calculation_version == CALCULATION_VERSION
    assert row.event_trading_date == T0
    assert row.baseline_price == 100.0
    assert row.return_m1 == pytest.approx(100 / 80 - 1)
    assert row.return_p1 == pytest.approx(0.10)
    assert row.return_p3 == pytest.approx(0.25)
    assert row.return_p5 == pytest.approx(0.50)
    assert row.return_p20 == pytest.approx(1.00)
    assert row.benchmark_return_p1 == pytest.approx(0.10)
    assert row.benchmark_return_p3 == pytest.approx(0.20)
    assert row.benchmark_return_p5 == pytest.approx(0.00)
    assert row.benchmark_return_p20 == pytest.approx(-0.10)
    assert row.relative_return_p1 == pytest.approx(0.00)
    assert row.relative_return_p3 == pytest.approx(0.05)
    assert row.relative_return_p5 == pytest.approx(0.50)
    assert row.relative_return_p20 == pytest.approx(1.10)
    assert row.event_day_volume == 2500
    assert row.pre_event_average_volume == 1000
    assert row.volume_ratio == pytest.approx(2.5)
    assert row.pre_event_volatility == pytest.approx(0.0)
    expected_window = sample_stdev(daily_returns([Decimal("100"), Decimal("110"), Decimal("120"), Decimal("125"), Decimal("140"), Decimal("150")]))
    assert row.event_window_volatility == pytest.approx(expected_window)
    assert row.calculation_job_id is None or row.first_market_date == PRIOR[0]


def test_missing_future_and_benchmark_stay_null(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc))
    seed_sessions(db, "NVDA", [(PRIOR[-1], "80", 1000), (T0, "100", 1000), (FUTURE[0], "110", 1000)])
    row = EventImpactCalculator(benchmark_symbol="SPY").calculate_event(db, event.id)
    impact = db.query(EventImpact).one()
    assert impact.return_p1 == pytest.approx(0.10)
    assert impact.return_p3 is None
    assert impact.return_p20 is None
    assert impact.benchmark_return_p1 is None
    assert impact.relative_return_p1 is None
    assert impact.volume_ratio is None
    assert impact.pre_event_volatility is None
    assert "benchmark" in row["impacts"][0]["unavailable"]


def test_weekend_event_uses_next_session(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    saturday = datetime(2026, 1, 31, 18, 0, tzinfo=timezone.utc)
    event = confirmed_event(db, nvda.entity_id, occurred_at=saturday)
    seed_sessions(db, "NVDA", [(date(2026, 1, 30), "80", 1000), (T0, "100", 1000), (FUTURE[0], "110", 1000)])
    EventImpactCalculator(benchmark_symbol="MISSING").calculate_event(db, event.id)
    impact = db.query(EventImpact).one()
    assert impact.event_trading_date == T0
    assert impact.return_p1 == pytest.approx(0.10)


def test_detected_event_is_skipped(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id)
    event.status = EventStatus.DETECTED.value
    db.flush()
    report = EventImpactCalculator().calculate_event(db, event.id)
    assert report["result"] == "skipped"
    assert db.query(EventImpact).count() == 0


def test_missing_baseline_is_unavailable_not_zero(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc))
    seed_sessions(db, "NVDA", [(PRIOR[-1], "80", 1000)])
    EventImpactCalculator(benchmark_symbol="MISSING").calculate_event(db, event.id)
    impact = db.query(EventImpact).one()
    assert impact.event_trading_date is None
    assert impact.baseline_price is None
    assert impact.return_p1 is None


def test_idempotent_single_and_batch(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc))
    seed_sessions(db, "NVDA", [(PRIOR[-1], "80", 1000), (T0, "100", 1000), (FUTURE[0], "110", 1000)])
    calc = EventImpactCalculator(benchmark_symbol="MISSING")
    first = calc.calculate_event(db, event.id)
    second = calc.calculate_event(db, event.id)
    assert first["impacts"][0]["result"] == "inserted"
    assert second["impacts"][0]["result"] == "updated"
    assert db.query(EventImpact).count() == 1
    batch1 = calc.calculate_batch(db)
    batch2 = calc.calculate_batch(db)
    assert batch1["inserted"] == 0 and batch1["updated"] == 1
    assert batch2["updated"] == 1
    assert db.query(EventImpact).count() == 1
    v2 = EventImpactCalculator(calculation_version="v2", benchmark_symbol="MISSING")
    v2.calculate_event(db, event.id)
    assert db.query(EventImpact).count() == 2
    versions = {row.calculation_version for row in db.query(EventImpact)}
    assert versions == {"v1", "v2"}


def test_calculator_does_not_write_market_bars(db):
    from app.domain.ingestion_models import MarketBar

    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc))
    seed_sessions(db, "NVDA", [(T0, "100", 1000)])
    before = db.query(MarketBar).count()
    EventImpactCalculator(benchmark_symbol="MISSING").calculate_event(db, event.id)
    assert db.query(MarketBar).count() == before

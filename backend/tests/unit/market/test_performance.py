"""Engineering timings for fixture-sized event intelligence work."""

from __future__ import annotations

import time

import pytest

from app.market.calculator import EventImpactCalculator
from app.market.candidates import EventCandidateService, propose_news_candidates
from tests.unit.market.helpers import (
    close_db,
    company,
    confirmed_event,
    migrated_db,
    seed_news,
    seed_sessions,
    weekdays,
)
from datetime import date, datetime, timezone


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_fixture_performance_is_measured(db, capsys):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    seed_news(db, tickers=["NVDA"])
    t0 = time.perf_counter()
    propose_news_candidates(db)
    news_ms = (time.perf_counter() - t0) * 1000
    candidate = EventCandidateService()
    from sqlalchemy import select
    from app.domain.models import EventCandidate

    row = db.scalars(select(EventCandidate)).one()
    t1 = time.perf_counter()
    candidate.accept(db, row.id, tickers=["NVDA"])
    assoc_ms = (time.perf_counter() - t1) * 1000

    event = confirmed_event(
        db, nvda.entity_id, occurred_at=datetime(2026, 2, 2, 15, 0, tzinfo=timezone.utc)
    )
    prior = weekdays(date(2026, 1, 5), 20)
    future = weekdays(date(2026, 2, 3), 5)
    seed_sessions(
        db,
        "NVDA",
        [(day, "100", 1000) for day in prior + [date(2026, 2, 2)] + future],
        start_n=8000,
    )
    calc = EventImpactCalculator(benchmark_symbol="MISSING")
    t2 = time.perf_counter()
    calc.calculate_event(db, event.id)
    single_ms = (time.perf_counter() - t2) * 1000
    t3 = time.perf_counter()
    calc.calculate_batch(db)
    batch_ms = (time.perf_counter() - t3) * 1000
    print(
        f"PERF news_propose={news_ms:.2f}ms associate={assoc_ms:.2f}ms "
        f"single_impact={single_ms:.2f}ms batch_impact={batch_ms:.2f}ms"
    )
    assert news_ms < 5000
    assert single_ms < 5000

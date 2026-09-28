"""Quality CLI, read-only APIs, and metric label policy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.session import init_db
from app.domain.ingestion_models import MarketBar
from app.domain.models import Event, EventCandidate
from app.main import app
from app.market.candidates import EventCandidateService, propose_news_candidates
from app.market.quality import market_quality
from app.pipeline.metrics import FORBIDDEN_LABELS, MetricLabelError
from tests.unit.market.helpers import close_db, company, confirmed_event, migrated_db, seed_news, seed_sessions

client = TestClient(app)


@pytest.fixture()
def db(tmp_path):
    session = migrated_db(tmp_path)
    try:
        yield session
        session.rollback()
    finally:
        close_db(session)


def test_quality_is_read_only(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    confirmed_event(db, nvda.entity_id)
    before_events = db.scalar(select(Event).limit(1)).id
    report = market_quality(db)
    assert report["read_only"] is True
    assert report["events"]["total"] == 1
    assert db.scalar(select(Event).limit(1)).id == before_events
    assert db.query(MarketBar).count() == 0


def test_quality_flags_missing_timestamp_and_invalid_ohlc(db):
    nvda = company(db, "NVIDIA Corporation", "NVDA", "1045810")
    event = confirmed_event(db, nvda.entity_id)
    event.occurred_at = None
    event.published_at = None
    db.flush()
    report = market_quality(db)
    assert report["events"]["missing_timestamp"]["count"] == 1


def test_api_event_and_entity_routes(db):
    init_db()
    missing = client.get("/api/events/00000000-0000-0000-0000-000000000001")
    assert missing.status_code == 404
    assert client.get("/api/graph/cypher").status_code == 404


def test_metrics_reject_high_cardinality_labels():
    from app.market.metrics import MARKET_METRICS

    with pytest.raises(MetricLabelError):
        MARKET_METRICS.inc("events_created_total", event_id="abc")
    with pytest.raises(MetricLabelError):
        MARKET_METRICS.inc("market_impact_calculations_total", symbol="NVDA")
    for key in ("event_id", "entity_id", "symbol", "ticker", "cik", "source_id"):
        assert key in FORBIDDEN_LABELS


def test_cli_parser():
    from app.market.__main__ import build_parser

    parser = build_parser()
    args = parser.parse_args(["impact", "--since", "2026-01-01", "--until", "2026-02-01"])
    assert args.since == "2026-01-01"
    args = parser.parse_args(["quality"])
    assert args.func.__name__ == "_quality"


def test_news_candidate_does_not_create_event_until_accept(db):
    company(db, "NVIDIA Corporation", "NVDA", "1045810")
    seed_news(db, tickers=["NVDA"])
    propose_news_candidates(db)
    assert db.scalar(select(EventCandidate)).status == "PENDING"
    assert db.scalar(select(Event)) is None
    EventCandidateService().accept(db, db.scalar(select(EventCandidate)).id, tickers=["NVDA"])
    assert db.scalar(select(Event)) is not None

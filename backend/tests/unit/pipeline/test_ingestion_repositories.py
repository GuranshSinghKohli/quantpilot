"""Tracking and canonical upserts against a throwaway SQLite file migrated by
Alembic. ``tests/integration/test_ingestion_repositories.py`` re-runs these
cases on PostgreSQL by overriding the ``repo_db`` fixture. All values are SYNTHETIC."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import IngestionRecordStatus as S, IngestionRunStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRun, MarketBar, NewsArticle
from app.domain.ingestion_schemas import CompanyListingIn, MarketBarIn, NewsArticleIn
from app.domain.models import Company, Entity, Source
from app.pipeline.contracts import SOURCES
from app.pipeline.tracking import RunTracker, run_summary
from app.repositories import ingestion as repo
from app.repositories.canonical import upsert_companies, upsert_market_bars, upsert_news

T0 = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def repo_db(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'pipeline.db'}")
    upgrade_to_head(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _hex(n: int) -> str:
    return f"{n:064x}"


class Env:
    """A source, a run and a processing job, plus helpers to register events."""

    def __init__(self, db: Session, source_key: str):
        spec = SOURCES[source_key]
        self.db = db
        self.source = repo.ensure_source(
            db, key=spec.key, domain=spec.domain.value, provider=spec.provider, description=spec.description
        )
        self.run = repo.start_run(db, source_id=self.source.id, trigger="test", params={})
        self.job = repo.start_processing_job(
            db, job_id=uuid.uuid4(), job_name=f"test_{spec.domain.value}", domain=spec.domain.value, mode="full"
        )
        self.domain = spec.domain.value

    def event(self, n: int) -> str:
        ref = repo.EventRef(
            event_id=_hex(n), domain=self.domain, external_id=f"ext-{n}", dedup_key=f"key-{n}",
            content_hash=_hex(10_000 + n), schema_version=1,
        )
        assert repo.claim_event(self.db, ref, run_id=self.run.id, source_id=self.source.id, topic="test.topic") is repo.Claim.NEW
        return ref.event_id


# --- tracking --------------------------------------------------------------------


def test_ensure_source_is_idempotent(repo_db):
    a = Env(repo_db, "massive.market_daily").source
    b = repo.ensure_source(repo_db, key=a.key, domain=a.domain, provider=a.provider, description=a.description)
    assert a.id == b.id


def test_claim_event_new_duplicate_and_retry(repo_db):
    env = Env(repo_db, "massive.market_daily")
    ref = repo.EventRef(_hex(1), "market", "x", "k", _hex(2), 1)
    claim = lambda: repo.claim_event(repo_db, ref, run_id=env.run.id, source_id=env.source.id, topic="t")

    assert claim() is repo.Claim.NEW
    assert claim() is repo.Claim.RETRY  # still pending: never confirmed delivered

    assert repo.advance_records(repo_db, S.PUBLISH_FAILED, [ref.event_id], reason="broker down") == 1
    assert claim() is repo.Claim.RETRY
    record = repo.get_record(repo_db, ref.event_id)
    repo_db.refresh(record)
    assert (record.status, record.status_reason) == (S.PENDING.value, None)

    assert repo.record_deliveries(repo_db, [(ref.event_id, 2, 41)]) == 1
    assert claim() is repo.Claim.DUPLICATE
    repo_db.refresh(record)
    assert (record.status, record.kafka_partition, record.kafka_offset) == (S.PUBLISHED.value, 2, 41)


def test_advance_records_never_moves_backwards(repo_db):
    env = Env(repo_db, "massive.market_daily")
    eid = env.event(1)
    assert repo.advance_records(repo_db, S.LANDED, [eid], raw_uri="s3://b/raw/x.json") == 1
    assert repo.advance_records(repo_db, S.PUBLISHED, [eid]) == 0
    assert repo.advance_records(repo_db, S.DLQ, [eid]) == 0
    assert repo.advance_records(repo_db, S.LOADED, [eid], processing_job_id=env.job.id) == 1
    assert repo.advance_records(repo_db, S.PROCESSED, [eid]) == 0
    record = repo.get_record(repo_db, eid)
    repo_db.refresh(record)
    assert (record.status, record.raw_uri, record.processing_job_id) == (S.LOADED.value, "s3://b/raw/x.json", env.job.id)


def test_run_tracker_records_counts_status_and_duration(repo_db):
    tracker = RunTracker(lambda: nullcontext(repo_db), SOURCES["massive.market_daily"], params={"symbols": ["SYNTH"]})
    run_id = tracker.start()
    tracker.counters.received, tracker.counters.valid, tracker.counters.invalid = 3, 2, 1
    tracker.counters.published, tracker.counters.duplicate = 1, 1
    assert tracker.finish() is IngestionRunStatus.PARTIAL

    summary = run_summary(repo_db, run_id)
    assert summary["status"] == "PARTIAL"
    assert (summary["received"], summary["valid"], summary["invalid"]) == (3, 2, 1)
    assert summary["duration_ms"] >= 0 and summary["finished_at"]
    assert summary["params"] == {"symbols": ["SYNTH"]}
    assert summary["error"] is None


def test_run_tracker_flags_broken_bookkeeping(repo_db):
    tracker = RunTracker(lambda: nullcontext(repo_db), SOURCES["massive.news"])
    tracker.start()
    tracker.counters.received = 2  # never classified as valid or invalid
    tracker.finish()
    run = repo_db.get(IngestionRun, tracker.run_id)
    assert run.status == "FAILED"
    assert "counter invariants violated" in run.error


def test_run_summary_reads_downstream_progress(repo_db):
    env = Env(repo_db, "massive.market_daily")
    ids = [env.event(n) for n in range(1, 5)]
    repo.record_deliveries(repo_db, [(i, 0, n) for n, i in enumerate(ids)])
    repo.advance_records(repo_db, S.LANDED, ids[:3])
    repo.advance_records(repo_db, S.DLQ, ids[3:])
    repo.advance_records(repo_db, S.LOADED, ids[:1], processing_job_id=env.job.id)
    s = run_summary(repo_db, env.run.id)
    assert (s["landed"], s["dlq"], s["processed"], s["loaded"]) == (3, 1, 1, 1)


def test_processing_job_lifecycle_and_lineage(repo_db):
    env = Env(repo_db, "massive.market_daily")
    eid = env.event(7)
    repo.record_deliveries(repo_db, [(eid, 1, 99)])
    repo.advance_records(repo_db, S.LANDED, [eid], raw_uri="s3://lake/raw/market/x.json")
    metrics = {"input_rows": 1, "rejected_rows": 0, "duplicates_removed": 0, "output_rows": 1,
               "output_partitions": ["year=2026/month=09/day=01"]}
    repo.finish_processing_job(repo_db, env.job, status=ProcessingJobStatus.COMPLETED, metrics=metrics,
                               output_uri="s3://lake/processed/market/")
    repo.record_load(repo_db, env.job, loaded_rows=1, duration_ms=5)
    repo.advance_records(repo_db, S.LOADED, [eid], processing_job_id=env.job.id)

    trace = repo.lineage(repo_db, eid)
    assert trace["source"]["key"] == "massive.market_daily"
    assert trace["ingestion_run"]["id"] == str(env.run.id)
    assert trace["raw_record"]["kafka"] == {"topic": "test.topic", "partition": 1, "offset": 99}
    assert trace["raw_record"]["raw_uri"] == "s3://lake/raw/market/x.json"
    assert trace["processing_job"]["status"] == "COMPLETED"
    assert env.job.output_partitions == ["year=2026/month=09/day=01"]
    assert repo.lineage(repo_db, _hex(424242)) is None


# --- canonical upserts ---------------------------------------------------------


def _bar(env: Env, n: int, *, close="105", at=T0, day=date(2026, 9, 1), symbol="SYNTH") -> MarketBarIn:
    return MarketBarIn(
        source_ingested_at=at, event_id=env.event(n), processing_job_id=env.job.id,
        raw_uri=f"s3://lake/raw/market/{n}.json", content_hash=_hex(20_000 + n),
        symbol=symbol, interval="1d", trading_date=day, bar_ts=datetime.combine(day, datetime.min.time(), timezone.utc),
        open=Decimal("100"), high=Decimal("110"), low=Decimal("95"), close=Decimal(close),
        adj_close=Decimal(close), volume=1000, currency="USD", source="massive",
    )


def test_market_bars_newest_wins_and_reload_is_a_no_op(repo_db):
    env = Env(repo_db, "massive.market_daily")
    first = [_bar(env, 1), _bar(env, 2, day=date(2026, 9, 2))]
    assert upsert_market_bars(repo_db, first).written == 2

    again = upsert_market_bars(repo_db, first)
    assert (again.written, again.unchanged) == (0, 2)

    newer = _bar(env, 3, close="106", at=T0 + timedelta(hours=1))
    older = _bar(env, 4, close="90", at=T0 - timedelta(hours=1))
    stats = upsert_market_bars(repo_db, [newer, older])
    assert (stats.written, stats.unchanged) == (1, 1)
    assert upsert_market_bars(repo_db, [_bar(env, 5, close="1", at=T0 - timedelta(days=1))]).written == 0

    rows = repo_db.scalars(select(MarketBar).order_by(MarketBar.trading_date)).all()
    assert len(rows) == 2
    repo_db.refresh(rows[0])
    assert rows[0].close == Decimal("106") and rows[0].event_id == newer.event_id


def test_market_bars_tie_on_ingested_at_breaks_by_event_id(repo_db):
    env = Env(repo_db, "massive.market_daily")
    low, high = _bar(env, 10, close="101"), _bar(env, 11, close="102")
    assert upsert_market_bars(repo_db, [high]).written == 1
    assert upsert_market_bars(repo_db, [low]).written == 0
    assert repo_db.scalar(select(MarketBar.close)) == Decimal("102")


def _news(env: Env, n: int, *, title="Synthetic headline", at=T0, url="https://example.com/a?utm_source=x") -> NewsArticleIn:
    return NewsArticleIn(
        source_ingested_at=at, event_id=env.event(n), processing_job_id=env.job.id,
        raw_uri=f"s3://lake/raw/news/{n}.json", content_hash=_hex(30_000 + n),
        source_key="massive.news", external_id=f"news-{n}", url=url, title=title,
        publisher="Example Wire", author=None, published_at=T0 - timedelta(hours=3),
        summary="Synthetic summary.", tickers=["SYNTH"],
    )


def test_news_upserts_into_sources_and_news_articles(repo_db):
    env = Env(repo_db, "massive.news")
    stats = upsert_news(repo_db, [_news(env, 1), _news(env, 2, url="https://example.com/b")])
    assert stats.written == 2
    tie_loser = _news(env, 0, title="Should not win", url="https://example.com/b")  # same time, lower event_id
    assert upsert_news(repo_db, [tie_loser]).written == 0
    tie_winner = _news(env, 3, title="Tie winner", url="https://example.com/b")
    assert upsert_news(repo_db, [tie_winner]).written == 1
    b = repo_db.scalars(select(Source).where(Source.url == "https://example.com/b")).one()
    repo_db.refresh(b)
    assert b.title == "Tie winner" and repo_db.get(NewsArticle, b.id).event_id == tie_winner.event_id

    updated = _news(env, 4, title="Corrected headline", at=T0 + timedelta(hours=1), url="https://EXAMPLE.com/a")
    assert upsert_news(repo_db, [updated]).written == 1

    assert repo_db.scalar(select(func.count()).select_from(Source)) == 2
    assert repo_db.scalar(select(func.count()).select_from(NewsArticle)) == 2
    source = repo_db.scalars(select(Source).where(Source.url == "https://example.com/a")).one()
    repo_db.refresh(source)
    assert source.title == "Corrected headline" and source.source_type == "news"
    article = repo_db.get(NewsArticle, source.id)
    repo_db.refresh(article)
    assert article.event_id == updated.event_id and article.tickers == ["SYNTH"]


def _company(env: Env, n: int, *, cik="0000000123", name="Synthetic Holdings Inc.", ticker="SYNA", at=T0) -> CompanyListingIn:
    return CompanyListingIn(
        source_ingested_at=at, event_id=env.event(n), processing_job_id=env.job.id,
        raw_uri=f"s3://lake/raw/company/{n}.json", content_hash=_hex(40_000 + n),
        cik=cik, name=name, ticker=ticker, exchange="nasdaq",
    )


def test_companies_resolve_by_cik_and_newest_wins(repo_db):
    env = Env(repo_db, "sec.company_tickers")
    assert upsert_companies(repo_db, [_company(env, 1)]).written == 1
    assert upsert_companies(repo_db, [_company(env, 2, ticker="OLD", at=T0 - timedelta(days=1))]).written == 0
    renamed = _company(env, 3, name="Synthetic Holdings Corp.", ticker="SYNC", at=T0 + timedelta(days=1))
    assert upsert_companies(repo_db, [renamed]).written == 1

    company = repo_db.scalars(select(Company).where(Company.cik == "0000000123")).one()
    repo_db.refresh(company)
    assert (company.legal_name, company.ticker, company.exchange) == ("Synthetic Holdings Corp.", "SYNC", "NASDAQ")
    assert company.event_id == renamed.event_id
    assert repo_db.scalar(select(func.count()).select_from(Entity)) == 1


def test_company_name_collision_is_disambiguated_not_merged(repo_db):
    env = Env(repo_db, "sec.company_tickers")
    stats = upsert_companies(repo_db, [
        _company(env, 1, cik="0000000001", ticker="AAA"),
        _company(env, 2, cik="0000000002", ticker="BBB"),
    ])
    assert stats.written == 2
    names = set(repo_db.scalars(select(Entity.canonical_name)))
    assert names == {"Synthetic Holdings Inc.", "Synthetic Holdings Inc. (CIK 0000000002)"}


def test_company_constraint_violation_is_reported_not_swallowed(repo_db):
    env = Env(repo_db, "sec.company_tickers")
    stats = upsert_companies(repo_db, [
        _company(env, 1, cik="0000000001", name="Alpha Synthetic", ticker="DUP"),
        _company(env, 2, cik="0000000002", name="Beta Synthetic", ticker="DUP"),
    ])
    assert stats.written == 1
    assert len(stats.failed) == 1 and stats.failed[0]["cik"] == "0000000002"
    assert repo_db.scalar(select(func.count()).select_from(Company)) == 1

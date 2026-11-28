"""End to end with real Spark: raw → process → load → process again → load again.

Runs on the tmp SQLite and, when TEST_DATABASE_URL is set, on PostgreSQL too
(tables truncated around each test). SYNTHETIC data only.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.db.models import Base
from app.domain.enums import CanonicalLoadStatus as L, IngestionRecordStatus as S, ProcessingJobStatus
from app.domain.ingestion_models import MarketBar, NewsArticle
from app.domain.models import Company, Source
from app.pipeline.lake import LocalObjectStore
from app.pipeline.load import CanonicalLoadRunner, ManifestMismatch
from app.repositories.canonical import upsert_companies
from tests.unit.pipeline.factories import company_record, event, market_record, news_record
from tests.unit.pipeline.load_env import LoadEnv
from tests.unit.pipeline.pipeline_env import migrated_sessions

DAY2 = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
DAY3 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def _truncate(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " CASCADE"))


@pytest.fixture(params=["sqlite", "postgresql"])
def e2e(request, tmp_path):
    store = LocalObjectStore(tmp_path / "lake")
    if request.param == "sqlite":
        yield LoadEnv(store, migrated_sessions(tmp_path / "db.sqlite"))
        return
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL not set to PostgreSQL")
    engine = build_engine(TEST_DATABASE_URL)
    upgrade_to_head(engine)
    _truncate(engine)
    try:
        yield LoadEnv(store, sessionmaker(bind=engine, expire_on_commit=False))
    finally:
        _truncate(engine)
        engine.dispose()


def news(n=1, **overrides):
    fields = dict(external_id=f"synthetic-news-{n}", url=f"https://example.com/synthetic/{n}", ingested_at=DAY2)
    fields.update(overrides)
    return event(news_record(**fields))


def market(day=1, **overrides):
    fields = dict(trading_date=date(2026, 9, day), timestamp=datetime(2026, 9, day, 13, 30, tzinfo=timezone.utc),
                  ingested_at=DAY2)
    fields.update(overrides)
    return event(market_record(**fields))


def company(cik="123", **overrides):
    fields = dict(external_id=cik, cik=cik, ingested_at=DAY2)
    fields.update(overrides)
    return event(company_record(**fields))


def _load(env: LoadEnv, job_id, **kwargs):
    return CanonicalLoadRunner(store=env.store, session_factory=env.sessions, **kwargs).run(job_id)


def _canonical(env: LoadEnv):
    with env.sessions() as db:
        return {
            "market": sorted((b.symbol, str(b.trading_date), b.event_id, str(b.processing_job_id))
                             for b in db.query(MarketBar)),
            "news": sorted((a.event_id, str(a.processing_job_id)) for a in db.query(NewsArticle)),
            "sources": sorted((s.url_hash, s.content_hash) for s in db.query(Source)),
            "company": sorted((c.cik, c.event_id, tuple(c.tickers or ()), str(c.processing_job_id))
                              for c in db.query(Company)),
        }


def test_process_load_process_load_converges(e2e, run):
    events = {
        "news": [news(1), news(2, published_at=datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc))],
        "market": [market(1), market(2), market(3)],
        "company": [company("123"), company("456", name="Other Synthetic Corp.", ticker="OTHR", tickers=["OTHR"])],
    }
    for batch in events.values():
        e2e.land(*batch)
    raw_before = e2e.snapshot("raw/")

    first = {d: run(e2e, e2e.plan(d)) for d in events}
    assert {r.status for r in first.values()} == {ProcessingJobStatus.COMPLETED}
    processed_after_first = e2e.snapshot("processed/")
    loads = {d: _load(e2e, first[d].job_id) for d in events}
    for d, result in loads.items():
        assert result.status is L.COMPLETE
        assert (result.counts.inserted, result.counts.candidates) == (len(events[d]), len(events[d]))
    assert e2e.snapshot("processed/") == processed_after_first  # the loader never writes processed data
    state = _canonical(e2e)
    assert len(state["market"]) == 3 and len(state["news"]) == 2 and len(state["company"]) == 2
    assert {row[3] for row in state["market"]} == {str(first["market"].job_id)}
    assert ("0000000123", events["company"][0].event_id, ("SYNA", "SYNB"), str(first["company"].job_id)) in state["company"]

    second = {d: run(e2e, e2e.plan(d)) for d in events}  # reprocess the same raw objects
    reloads = {d: _load(e2e, second[d].job_id) for d in events}
    for d, result in reloads.items():
        assert result.status is L.COMPLETE
        assert (result.counts.duplicates, result.counts.inserted, result.counts.updated) == (len(events[d]), 0, 0)
    assert _canonical(e2e) == state  # same winners, original lineage
    assert e2e.snapshot("raw/") == raw_before
    records = e2e.records()
    assert {records[ev.event_id].status for batch in events.values() for ev in batch} == {S.LOADED.value}

    # The first job's files were rewritten by the second job: its manifest no longer matches.
    with pytest.raises(ManifestMismatch, match="load that job instead"):
        _load(e2e, first["market"].job_id)
    assert _canonical(e2e) == state


def test_incremental_correction_flows_through_to_postgres(e2e, run):
    v1, other = news(1, title="Original headline"), news(2)
    e2e.land(v1, other)
    first = run(e2e, e2e.plan("news"))
    assert _load(e2e, first.job_id).counts.inserted == 2

    v1b = news(1, title="Corrected headline", ingested_at=DAY3)
    e2e.land(v1b)
    second = run(e2e, e2e.plan("news", start=date(2026, 9, 3)))
    result = _load(e2e, second.job_id)
    # The rewritten partition holds v1b (new) and `other` (kept from the first job).
    assert (result.counts.updated, result.counts.duplicates, result.counts.candidates) == (1, 1, 2)
    with e2e.sessions() as db:
        titles = sorted(s.title for s in db.query(Source))
        article = db.query(NewsArticle).filter(NewsArticle.event_id == v1b.event_id).one()
        assert str(article.processing_job_id) == str(second.job_id)
    assert titles == ["Corrected headline", "Synthetic headline for tests"]
    assert e2e.records()[v1.event_id].status == S.LOADED.value  # was canonical once; statuses never go back


def test_spark_and_the_canonical_guard_pick_the_same_tie_winner(e2e, run):
    a = company("123", name="Tie Alpha Inc.")
    b = company("123", name="Tie Beta Inc.")
    assert a.ingested_at == b.ingested_at and a.event_id != b.event_id
    e2e.land(a, b)
    job = run(e2e, e2e.plan("company"))
    assert job.counts.superseded_rows == 1
    assert _load(e2e, job.job_id).status is L.COMPLETE
    spark_winner = _canonical(e2e)["company"][0][1]
    assert spark_winner == max(a.event_id, b.event_id)

    # Feeding both versions straight to the canonical upsert keeps the same one.
    from app.pipeline.load.domains import CompanyLoader

    loader = CompanyLoader()
    rows = [loader.to_row(e2e.row(ev, job.job_id)) for ev in (a, b)]
    with e2e.sessions() as db:
        stats = upsert_companies(db, rows)
        db.rollback()
    assert stats.current == [spark_winner]


@pytest.mark.s3
def test_minio_process_then_load(s3_env, run, tmp_path):
    env = LoadEnv(s3_env.store, s3_env.sessions)
    bars = [market(1), market(2)]
    env.land(*bars)
    job = run(env, env.plan("market"))
    result = _load(env, job.job_id)
    assert result.status is L.COMPLETE and result.counts.inserted == 2
    assert result.report_uri.startswith("s3://")
    assert _load(env, job.job_id).counts.duplicates == 2
    with env.sessions() as db:
        raw_uris = sorted(b.raw_uri for b in db.query(MarketBar))
    assert all(uri.startswith("s3://") for uri in raw_uris)
    assert raw_uris == sorted(env.records()[b.event_id].raw_uri for b in bars)

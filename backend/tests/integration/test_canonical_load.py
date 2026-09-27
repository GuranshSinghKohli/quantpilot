"""Canonical loading on PostgreSQL: the unit cases again (real ON CONFLICT, row
locks, JSONB, FKs, advisory lock), plus concurrency cases SQLite cannot show.

The loader commits per batch from many sessions, so these tests use a committing
session factory on the disposable test database and truncate the tables around
each test instead of the SAVEPOINT-rollback ``db`` fixture.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.domain.ingestion_models import NewsArticle
from app.domain.ingestion_schemas import NewsArticleIn
from app.domain.models import Source
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake.keys import processed_prefix
from app.pipeline.load.domains import NewsLoader
from app.pipeline.spark.jobs.lock import JobAlreadyRunning, output_lock
from app.repositories.canonical import OutcomeConflict, upsert_news
from tests.unit.pipeline.load_env import LoadEnv
from tests.unit.pipeline.test_canonical_load import *  # noqa: F401,F403
from tests.unit.pipeline.test_canonical_load import INGESTED_AT, article, bar, counts, outcome


def _truncate(engine) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture()
def load_env(tmp_path, pg_engine):
    _truncate(pg_engine)
    yield LoadEnv(LocalObjectStore(tmp_path / "lake"), sessionmaker(bind=pg_engine, expire_on_commit=False))
    _truncate(pg_engine)


def test_load_is_refused_while_processing_holds_the_domain_lock(load_env):
    job_id = load_env.processed("market", [bar(1)])
    with output_lock(load_env.sessions, processed_prefix("market")):
        with pytest.raises(JobAlreadyRunning, match="processing or load job"):
            load_env.load(job_id)
    assert load_env.job(job_id).load_status is None  # refused before an attempt started
    assert counts(load_env.load(job_id)) == outcome(candidates=1, inserted=1)


def _row(env: LoadEnv, ev, job_id) -> NewsArticleIn:
    return NewsLoader().to_row(env.row(ev, job_id))


def test_concurrent_writer_bypassing_the_lock_cannot_corrupt_or_misreport(load_env):
    """Writer A holds a newer version uncommitted; writer B (older version) blocks
    on the row lock, then its guarded upsert writes nothing. B's pre-read saw no
    row, so it raises instead of reporting a wrong outcome, and rolls back."""
    url = "https://example.com/synthetic/race"
    older, newer = article(1, title="Older", url=url), article(1, title="Newer", url=url, at=INGESTED_AT + timedelta(hours=1))
    job_id = load_env.processed("news", [older, newer])
    rows = {ev.event_id: _row(load_env, ev, job_id) for ev in (older, newer)}

    a = load_env.sessions()
    assert upsert_news(a, [rows[newer.event_id]]).inserted == 1  # uncommitted
    outcome_b = {}

    def writer_b():
        with load_env.sessions() as b:
            try:
                upsert_news(b, [rows[older.event_id]])
                b.commit()
                outcome_b["result"] = "committed"
            except OutcomeConflict as exc:
                b.rollback()
                outcome_b["result"] = f"conflict: {exc}"

    thread = threading.Thread(target=writer_b)
    thread.start()
    time.sleep(0.5)
    assert thread.is_alive()  # blocked on A's uncommitted source row
    a.commit()
    a.close()
    thread.join(timeout=10)
    assert outcome_b["result"].startswith("conflict")

    source = load_env.all(Source)[0]
    with load_env.sessions() as db:
        article_row = db.get(NewsArticle, source.id)
    assert (source.title, source.content_hash, article_row.event_id) == ("Newer", newer.content_hash, newer.event_id)
    assert load_env.count(Source) == 1 and load_env.count(NewsArticle) == 1

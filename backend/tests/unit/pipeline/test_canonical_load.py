"""Canonical loading: processed Parquet (manifest-listed) → validation → newest-wins
upserts. Runs on a migrated tmp SQLite here and on PostgreSQL via
``tests/integration/test_canonical_load.py`` (same cases, real ON CONFLICT and
row locks). No JVM: the Parquet is written with pyarrow in the processed schema;
the spark tier covers real Spark output. All values are SYNTHETIC.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pyarrow as pa
import pytest

from app.domain.enums import CanonicalLoadStatus as L, IngestionRecordStatus as S
from app.domain.ingestion_models import MarketBar, NewsArticle, ProcessingJob
from app.domain.models import Company, Entity, Source
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake.keys import load_report_key, manifest_key, processed_prefix
from app.pipeline.load import (
    CanonicalLoadRunner,
    LoadConfigError,
    LoadValidationError,
    ManifestMismatch,
    MarketLoader,
    ReconciliationError,
    select_jobs,
)
from app.pipeline.load.validation import synthetic_allowed
from tests.unit.pipeline.factories import INGESTED_AT, company_record, event, market_record, news_record
from tests.unit.pipeline.load_env import LoadEnv, arrow_schema
from tests.unit.pipeline.pipeline_env import migrated_sessions

UTC = timezone.utc


@pytest.fixture()
def load_env(tmp_path):
    return LoadEnv(LocalObjectStore(tmp_path / "lake"), migrated_sessions(tmp_path / "db.sqlite"))


def co(cik="123", *, name="Synthetic Holdings Inc.", tickers=("SYNA", "SYNB"), exchange="Nasdaq", at=INGESTED_AT):
    return event(company_record(external_id=cik, cik=cik, name=name, ticker=tickers[0] if tickers else None,
                                tickers=list(tickers), exchange=exchange, ingested_at=at))


def bar(day=1, *, close=105.0, at=INGESTED_AT, symbol="SYNTH", source="massive.market_daily"):
    return event(market_record(source=source, symbol=symbol, trading_date=date(2026, 9, day),
                               timestamp=datetime(2026, 9, day, 13, 30, tzinfo=UTC),
                               close=close, adj_close=close, ingested_at=at))


def article(n=1, *, title="Synthetic headline", at=INGESTED_AT, url=None, published=None):
    return event(news_record(external_id=f"synthetic-news-{n}", title=title, ingested_at=at,
                             url=url or f"https://example.com/synthetic/{n}?utm_source=feed",
                             published_at=published or datetime(2026, 9, 1, 9, 0, tzinfo=UTC)))


def counts(result):
    c = result.counts
    return {"candidates": c.candidates, "inserted": c.inserted, "updated": c.updated, "stale": c.stale,
            "duplicates": c.duplicates, "failed": c.failed}


def outcome(candidates=0, inserted=0, updated=0, stale=0, duplicates=0, failed=0):
    return {"candidates": candidates, "inserted": inserted, "updated": updated, "stale": stale,
            "duplicates": duplicates, "failed": failed}


# --- each domain, with lineage ------------------------------------------------------


def test_company_load_keeps_identity_tickers_and_lineage(load_env):
    events = [co("123"), co("456", name="Other Synthetic Corp.", tickers=("OTHR",))]
    job_id = load_env.processed("company", events)
    result = load_env.load(job_id)

    assert result.status is L.COMPLETE
    assert counts(result) == outcome(candidates=2, inserted=2)
    by_cik = {c.cik: c for c in load_env.all(Company, Company.cik)}
    first = by_cik["0000000123"]
    assert (first.legal_name, first.ticker, first.tickers, first.exchange) == (
        "Synthetic Holdings Inc.", "SYNA", ["SYNA", "SYNB"], "NASDAQ")
    ev = events[0]
    assert (first.event_id, first.processing_job_id, first.content_hash, first.source) == (
        ev.event_id, job_id, ev.content_hash, "sec.company_tickers")
    assert first.raw_uri == load_env.store.uri(load_env.row(ev, job_id)["raw_key"])
    assert first.source_ingested_at.replace(tzinfo=UTC) == ev.ingested_at

    records = load_env.records()
    assert {records[e.event_id].status for e in events} == {S.LOADED.value}
    assert result.counts.records_loaded == 2

    job = load_env.job(job_id)
    assert (job.status, job.load_status, job.load_attempts) == ("COMPLETED", L.COMPLETE.value, 1)
    assert (job.load_candidates, job.load_inserted, job.loaded_rows, job.load_failed) == (2, 2, 2, 0)
    assert job.load_partitions == ["year=2026/month=09/day=02"] and job.loaded_at is not None
    report_key = load_report_key(job_id, result.attempt_id)
    assert job.load_report_uri == load_env.store.uri(report_key)
    report = load_env.json(report_key)
    assert report["status"] == L.COMPLETE.value and report["counts"]["inserted"] == 2
    assert report["manifest_uri"] == job.manifest_uri


def test_market_load_maps_every_column(load_env):
    events = [bar(1), bar(2, close=106.5)]
    job_id = load_env.processed("market", events)
    result = load_env.load(job_id)

    assert result.status is L.COMPLETE and counts(result) == outcome(candidates=2, inserted=2)
    assert result.partitions == ["year=2026/month=09/day=01", "year=2026/month=09/day=02"]
    rows = load_env.all(MarketBar, MarketBar.trading_date)
    first = rows[0]
    assert (first.symbol, first.interval, first.trading_date, first.source) == (
        "SYNTH", "1d", date(2026, 9, 1), "massive.market_daily")
    assert (first.open, first.high, first.low, first.close, first.volume) == (
        Decimal("100"), Decimal("110"), Decimal("95"), Decimal("105"), 1000)
    assert rows[1].close == Decimal("106.5")
    assert (first.event_id, first.processing_job_id, first.content_hash) == (
        events[0].event_id, job_id, events[0].content_hash)
    assert first.raw_uri.endswith(f"{events[0].event_id}.json")


def test_news_load_writes_source_and_article_together(load_env):
    events = [article(1), article(2, url="https://example.com/synthetic/2")]
    job_id = load_env.processed("news", events)
    result = load_env.load(job_id)

    assert result.status is L.COMPLETE and counts(result) == outcome(candidates=2, inserted=2)
    sources = {s.url: s for s in load_env.all(Source, Source.url)}
    first = sources["https://example.com/synthetic/1"]  # tracking parameter normalized away
    assert (first.source_type, first.title, first.publisher, first.content_hash) == (
        "news", "Synthetic headline", "Example Wire", events[0].content_hash)
    assert first.raw_uri.endswith(f"{events[0].event_id}.json")
    with load_env.sessions() as db:
        news = db.get(NewsArticle, first.id)
        assert (news.event_id, news.processing_job_id, news.external_id, news.source_key, news.tickers) == (
            events[0].event_id, job_id, "synthetic-news-1", "massive.news", ["SYNTH"])


# --- replay, reprocessing, conflicts -------------------------------------------------


def test_reloading_the_same_manifest_is_a_no_op(load_env):
    job_id = load_env.processed("market", [bar(1), bar(2), bar(3)])
    first = load_env.load(job_id)
    before = [(b.id, b.event_id, b.updated_at) for b in load_env.all(MarketBar, MarketBar.trading_date)]

    again = load_env.load(job_id)
    assert again.status is L.COMPLETE
    assert counts(again) == outcome(candidates=3, duplicates=3)
    assert [(b.id, b.event_id, b.updated_at) for b in load_env.all(MarketBar, MarketBar.trading_date)] == before
    assert again.counts.records_loaded == 0  # already loaded
    job = load_env.job(job_id)
    assert (job.load_attempts, job.load_duplicates, job.load_inserted) == (2, 3, 0)
    assert first.report_uri != again.report_uri  # one write-once report per attempt


def test_same_event_in_two_jobs_is_a_duplicate(load_env):
    ev = co("123")
    first = load_env.processed("company", [ev])
    second = load_env.processed("company", [ev], land=False)
    assert counts(load_env.load(first)) == outcome(candidates=1, inserted=1)
    assert counts(load_env.load(second)) == outcome(candidates=1, duplicates=1)
    company = load_env.all(Company)[0]
    assert company.processing_job_id == first  # the row that first produced it keeps its lineage


def test_newer_ingestion_updates_and_older_never_overwrites(load_env):
    old = co("123", name="Synthetic Holdings Inc.")
    new = co("123", name="Synthetic Holdings Corp.", tickers=("SYNC",), at=INGESTED_AT + timedelta(hours=2))
    old_job, new_job = load_env.processed("company", [old]), load_env.processed("company", [new])

    assert counts(load_env.load(new_job)) == outcome(candidates=1, inserted=1)
    assert counts(load_env.load(old_job)) == outcome(candidates=1, stale=1)  # older after newer
    company = load_env.all(Company)[0]
    assert (company.legal_name, company.ticker, company.event_id) == ("Synthetic Holdings Corp.", "SYNC", new.event_id)

    load_env.load(old_job)
    assert counts(load_env.load(new_job)) == outcome(candidates=1, duplicates=1)
    assert load_env.count(Entity) == 1
    # the stale version's record stays processed: it never became canonical
    assert load_env.records()[old.event_id].status == S.PROCESSED.value


def test_newer_version_replaces_older_one_already_loaded(load_env):
    old = bar(1, close=105.0)
    new = bar(1, close=104.25, at=INGESTED_AT + timedelta(minutes=5))  # vendor correction
    assert counts(load_env.load(load_env.processed("market", [old]))) == outcome(candidates=1, inserted=1)
    assert counts(load_env.load(load_env.processed("market", [new]))) == outcome(candidates=1, updated=1)
    stored = load_env.all(MarketBar)[0]
    assert (stored.close, stored.event_id) == (Decimal("104.25"), new.event_id)
    assert load_env.count(MarketBar) == 1


def test_equal_ingestion_time_breaks_ties_by_larger_event_id_in_any_order(load_env):
    a, b = co("123", name="Tie Alpha Inc."), co("123", name="Tie Beta Inc.")
    assert a.ingested_at == b.ingested_at and a.event_id != b.event_id
    winner, loser = (a, b) if a.event_id > b.event_id else (b, a)
    loser_job, winner_job = load_env.processed("company", [loser]), load_env.processed("company", [winner])
    load_env.load(loser_job)
    assert counts(load_env.load(winner_job)) == outcome(candidates=1, updated=1)
    assert counts(load_env.load(loser_job)) == outcome(candidates=1, stale=1)
    assert load_env.all(Company)[0].event_id == winner.event_id


def test_versions_inside_one_load_collapse_to_the_newest(load_env):
    v1 = bar(1, close=101.0)
    v2 = bar(1, close=102.0, at=INGESTED_AT + timedelta(minutes=1))
    v3 = bar(1, close=103.0, at=INGESTED_AT + timedelta(minutes=2))
    result = load_env.load(load_env.processed("market", [v1, v3, v2]), batch_size=10)
    assert counts(result) == outcome(candidates=3, inserted=1, stale=2)
    assert load_env.all(MarketBar)[0].close == Decimal("103")


def test_company_identity_is_the_cik_never_the_name(load_env):
    same_name = [co("111", tickers=("AAA",)), co("222", tickers=("BBB",))]
    load_env.load(load_env.processed("company", same_name))
    names = {e.canonical_name for e in load_env.all(Entity)}
    assert names == {"Synthetic Holdings Inc.", "Synthetic Holdings Inc. (CIK 0000000222)"}

    renamed = co("111", name="Renamed Synthetic Inc.", tickers=("AAA",), at=INGESTED_AT + timedelta(hours=1))
    assert counts(load_env.load(load_env.processed("company", [renamed]))) == outcome(candidates=1, updated=1)
    assert load_env.count(Company) == 2 and load_env.count(Entity) == 2


def test_ticker_held_by_another_cik_fails_the_row_and_the_load(load_env):
    load_env.load(load_env.processed("company", [co("111", name="Alpha Synthetic", tickers=("DUP",))]))
    claim = co("222", name="Beta Synthetic", tickers=("DUP",), at=INGESTED_AT + timedelta(hours=1))
    other = co("333", name="Gamma Synthetic", tickers=("GMA",), at=INGESTED_AT + timedelta(hours=1))
    job_id = load_env.processed("company", [claim, other])
    result = load_env.load(job_id)

    assert result.status is L.FAILED
    assert counts(result) == outcome(candidates=2, inserted=1, failed=1)
    assert "0000000222" in result.error and claim.event_id in result.error
    assert {c.cik for c in load_env.all(Company)} == {"0000000111", "0000000333"}
    job = load_env.job(job_id)
    assert (job.status, job.load_status, job.load_failed) == ("COMPLETED", L.FAILED.value, 1)
    report = load_env.json(f"_manifests/loads/{job_id}/{result.attempt_id}.json")
    assert [f["cik"] for f in report["failures"] if f["pass"] == "retry"] == ["0000000222"]
    assert load_env.records()[claim.event_id].status == S.PROCESSED.value


def _ticker_move(load_env, batch_size):
    load_env.load(load_env.processed("company", [co("222", name="Old Holder", tickers=("MOVE",))]))
    later = INGESTED_AT + timedelta(hours=1)
    taker, releaser = co("111", name="New Holder", tickers=("MOVE",), at=later), co("222", name="Old Holder", tickers=("OLDH",), at=later)
    result = load_env.load(load_env.processed("company", [taker, releaser]), batch_size=batch_size)
    assert result.status is L.COMPLETE
    assert counts(result) == outcome(candidates=2, inserted=1, updated=1)
    assert {c.cik: c.ticker for c in load_env.all(Company)} == {"0000000111": "MOVE", "0000000222": "OLDH"}
    return result


def test_ticker_moving_between_ciks_in_one_batch(load_env):
    assert _ticker_move(load_env, batch_size=10).failures == []


def test_ticker_moving_between_ciks_across_batches_resolves_on_retry(load_env):
    # 111 sorts first and is alone in its batch, so it claims MOVE before 222 releases it.
    result = _ticker_move(load_env, batch_size=1)
    assert [(f["cik"], f["pass"]) for f in result.failures] == [("0000000111", "load")]


def test_news_version_conflict_keeps_source_and_article_consistent(load_env):
    url = "https://example.com/synthetic/story"
    v1 = article(1, title="First headline", url=url)
    v2 = article(1, title="Corrected headline", url=url + "?utm_source=x", at=INGESTED_AT + timedelta(hours=1),
                 published=datetime(2026, 9, 2, 8, 0, tzinfo=UTC))  # correction moves partitions
    job_id = load_env.processed("news", [v1, v2])
    result = load_env.load(job_id)
    # one file per partition, loaded in partition order: v1 inserted, then v2 replaces it
    assert counts(result) == outcome(candidates=2, inserted=1, updated=1)
    assert len(result.partitions) == 2

    source = load_env.all(Source)[0]
    with load_env.sessions() as db:
        news = db.get(NewsArticle, source.id)
    assert (source.title, source.content_hash, news.event_id) == ("Corrected headline", v2.content_hash, v2.event_id)
    assert source.published_at.replace(tzinfo=UTC) == v2.record.published_at
    assert load_env.count(Source) == 1 and load_env.count(NewsArticle) == 1

    older = load_env.processed("news", [v1], land=False)
    assert counts(load_env.load(older)) == outcome(candidates=1, stale=1)
    assert load_env.all(Source)[0].title == "Corrected headline"


def test_news_tie_on_ingestion_time_keeps_one_version_in_both_tables(load_env):
    url = "https://example.com/synthetic/tie"
    a, b = article(1, title="Tie A", url=url), article(1, title="Tie B", url=url)
    winner = max((a, b), key=lambda e: e.event_id)
    for ev in (winner, a if winner is b else b):  # winner first: the loser must not replace it
        load_env.load(load_env.processed("news", [ev]))
    source = load_env.all(Source)[0]
    with load_env.sessions() as db:
        news = db.get(NewsArticle, source.id)
    assert news.event_id == winner.event_id and source.title == winner.record.title
    assert source.content_hash == winner.content_hash


# --- validation: nothing written, nothing skipped --------------------------------


def _bad(key, value):
    def mutate(row):
        row[key] = value(row) if callable(value) else value
    return mutate


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (_bad("event_id", "f" * 64), "event_id does not match"),
        (_bad("dedup_key", "market:OTHER:1d:2026-09-01:massive.market_daily"), "dedup_key"),
        (_bad("low", Decimal("120")), "low 120.000000 > high"),
        (_bad("open", Decimal("200")), "open 200.000000 outside"),
        (_bad("volume", -1), "volume"),
        (_bad("symbol", None), "symbol is null"),
        (_bad("processing_job_id", "not-a-uuid"), "not a UUID"),
        (_bad("raw_uri", "s3://elsewhere/raw/market/x.json"), "does not point at raw_key"),
        (_bad("source", "massive.news"), "not a registered market source"),
        (_bad("synthetic", True), "disagrees with source"),
        (_bad("envelope_schema_version", 99), "envelope_schema_version 99"),
    ],
)
def test_invalid_processed_rows_fail_the_load_before_any_write(load_env, mutate, reason):
    good, bad = bar(1), bar(2)
    job_id = load_env.processed("market", [good, bad], mutate=lambda r: mutate(r) if r["event_id"] == bad.event_id else None)
    with pytest.raises(LoadValidationError, match=reason) as info:
        load_env.load(job_id)
    assert info.value.problems and all(p.row >= 0 for p in info.value.problems)
    assert load_env.count(MarketBar) == 0  # the valid row was not written either
    job = load_env.job(job_id)
    assert (job.status, job.load_status) == ("COMPLETED", L.FAILED.value)
    assert reason.split()[0] in job.load_error
    assert load_env.records()[good.event_id].status == S.PROCESSED.value


def test_untracked_event_and_unknown_job_are_lineage_failures(load_env):
    tracked = bar(1)
    untracked = bar(2)
    load_env.land(untracked, track=False)
    load_env.land(tracked)
    job_id = load_env.processed("market", [tracked, untracked], land=False,
                                mutate=_bad("processing_job_id", str(uuid.uuid4())))
    with pytest.raises(LoadValidationError) as info:
        load_env.load(job_id)
    text = str(info.value)
    assert "not a tracked ingestion record" in text and "is not a processing job" in text


def test_synthetic_sources_are_refused_unless_allowed_on_a_test_database(load_env):
    synthetic = bar(1, source="synthetic.market")
    job_id = load_env.processed("market", [synthetic])
    with pytest.raises(LoadValidationError, match="SYNTHETIC"):
        load_env.load(job_id)
    assert load_env.count(MarketBar) == 0
    result = load_env.load(job_id, allow_synthetic=True)  # tmp/test database
    assert result.status is L.COMPLETE and load_env.count(MarketBar) == 1


def test_allow_synthetic_is_refused_outside_a_test_database():
    class Bind:
        def __init__(self, name):
            self.url = type("U", (), {"database": name})()

    class Db:
        def __init__(self, name):
            self._bind = Bind(name)

        def get_bind(self):
            return self._bind

    assert synthetic_allowed(Db("quantpilot_test"))
    assert not synthetic_allowed(Db("quantpilot"))
    assert not synthetic_allowed(Db("/Users/x/quantpilot/backend/data/quantpilot.db"))


def test_schema_drift_fails_the_load(load_env):
    schema = arrow_schema("market").remove(arrow_schema("market").get_field_index("currency"))

    def drop_currency(row):
        row.pop("currency")

    job_id = load_env.processed("market", [bar(1)], mutate=drop_currency, schema=schema)
    with pytest.raises(LoadValidationError, match="missing column currency"):
        load_env.load(job_id)

    wrong = pa.schema([f if f.name != "volume" else pa.field("volume", pa.int32()) for f in arrow_schema("market")])
    job_id = load_env.processed("market", [bar(2)], schema=wrong)
    with pytest.raises(LoadValidationError, match="column volume is int32"):
        load_env.load(job_id)


# --- manifest, selection, reconciliation -----------------------------------------


def test_missing_or_replaced_files_fail_with_the_later_job_hint(load_env):
    job_id = load_env.processed("market", [bar(1)])
    key = next(k for k in load_env.keys(processed_prefix("market")) if k.endswith(".parquet"))
    (load_env.store.root / key).unlink()
    with pytest.raises(ManifestMismatch, match="load that job instead"):
        load_env.load(job_id)
    assert load_env.job(job_id).load_status == L.FAILED.value


def test_manifest_must_belong_to_the_job(load_env):
    job_id = load_env.processed("market", [bar(1)])
    with load_env.sessions() as db:
        db.get(ProcessingJob, job_id).manifest_uri = "s3://other/_manifests/processing/x.json"
        db.commit()
    with pytest.raises(ManifestMismatch, match="records manifest"):
        load_env.load(job_id)


def test_reconciliation_against_manifest_row_count(load_env):
    job_id = load_env.processed("market", [bar(1), bar(2)], output_rows=3)
    with pytest.raises(ReconciliationError, match="manifest output_rows 3"):
        load_env.load(job_id)
    job = load_env.job(job_id)
    assert job.load_status == L.FAILED.value and "reconciliation" in job.load_error


def test_partition_date_filter_loads_only_selected_partitions(load_env):
    job_id = load_env.processed("market", [bar(1), bar(2), bar(3)])
    result = load_env.load(job_id, since=date(2026, 9, 2), until=date(2026, 9, 2))
    assert result.partitions == ["year=2026/month=09/day=02"]
    assert counts(result) == outcome(candidates=1, inserted=1)
    assert [b.trading_date for b in load_env.all(MarketBar)] == [date(2026, 9, 2)]


def test_only_completed_jobs_load_and_selection_is_explicit(load_env):
    job_id = load_env.processed("company", [co("123")])
    with load_env.sessions() as db:
        with pytest.raises(LoadConfigError, match="exactly one"):
            select_jobs(db)
        with pytest.raises(LoadConfigError, match="exactly one"):
            select_jobs(db, job_id=job_id, latest=True, domain="company")
        with pytest.raises(LoadConfigError, match="needs --domain"):
            select_jobs(db, latest=True)
        with pytest.raises(LoadConfigError, match="not a processing manifest"):
            select_jobs(db, manifest="processed/company/x.parquet")
        with pytest.raises(LoadConfigError, match="not market"):
            select_jobs(db, job_id=job_id, domain="market")
        assert [j.id for j in select_jobs(db, manifest=load_env.store.uri(manifest_key(job_id)))] == [job_id]
        assert [j.id for j in select_jobs(db, latest=True, domain="company")] == [job_id]
        assert [j.domain for j in select_jobs(db, latest=True, domain="all")] == ["company"]
        with pytest.raises(LoadConfigError, match="no COMPLETED news"):
            select_jobs(db, latest=True, domain="news")
        db.get(ProcessingJob, job_id).status = "FAILED"
        db.commit()
    with pytest.raises(LoadConfigError, match="only COMPLETED"):
        load_env.load(job_id)
    assert load_env.job(job_id).load_status is None  # a refused selection is not a load attempt


def test_cli_refuses_ambiguous_or_missing_selection(capsys):
    from app.pipeline.__main__ import main

    assert main(["load"]) == 2
    assert main(["load", "--domain", "news"]) == 2
    assert main(["load", "--latest"]) == 2
    assert main(["load", "--job", str(uuid.uuid4()), "--latest", "--domain", "news"]) == 2
    assert main(["load", "--manifest", "raw/news/x.json"]) == 2
    assert main(["load", "--latest", "--domain", "news", "--batch-size", "0"]) == 2
    assert "never loads the whole lake" in capsys.readouterr().err


# --- failures, retries, crashes ---------------------------------------------------


def test_batch_failure_rolls_back_that_batch_and_retry_converges(load_env, monkeypatch):
    events = [bar(d) for d in (1, 2, 3, 4)]
    job_id = load_env.processed("market", events)
    real, calls = MarketLoader.upsert, {"n": 0}

    def flaky(self, db, rows):
        calls["n"] += 1
        stats = real(self, db, rows)
        if calls["n"] == 3:
            raise RuntimeError("synthetic database failure")
        return stats

    monkeypatch.setattr(MarketLoader, "upsert", flaky)
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        load_env.load(job_id, batch_size=1)
    assert load_env.count(MarketBar) == 2  # batches 1 and 2 committed, batch 3 rolled back
    job = load_env.job(job_id)
    assert job.load_status == L.FAILED.value and "synthetic database failure" in job.load_error

    monkeypatch.setattr(MarketLoader, "upsert", real)
    result = load_env.load(job_id, batch_size=1)
    assert result.status is L.COMPLETE
    assert counts(result) == outcome(candidates=4, inserted=2, duplicates=2)
    assert load_env.count(MarketBar) == 4
    assert {load_env.records()[e.event_id].status for e in events} == {S.LOADED.value}


def test_crash_after_commit_before_completion_is_recovered_by_rerun(load_env, monkeypatch):
    events = [co("111", tickers=("AAA",)), co("222", name="Second Synthetic", tickers=("BBB",))]
    job_id = load_env.processed("company", events)

    class Crash(BaseException):
        pass

    def die(batch):
        raise Crash()

    monkeypatch.setattr(CanonicalLoadRunner, "_fail", lambda *a, **k: None)  # the process is gone: no cleanup
    with pytest.raises(Crash):
        load_env.load(job_id, batch_size=1, after_commit=die)
    job = load_env.job(job_id)
    assert (job.load_status, job.load_attempts, load_env.count(Company)) == (L.RUNNING.value, 1, 1)

    monkeypatch.undo()
    result = load_env.load(job_id, batch_size=1)
    assert result.status is L.COMPLETE
    assert counts(result) == outcome(candidates=2, inserted=1, duplicates=1)
    job = load_env.job(job_id)
    assert (job.load_status, job.load_attempts) == (L.COMPLETE.value, 2)
    assert load_env.count(Company) == 2


def test_batch_size_must_be_positive(load_env):
    with pytest.raises(LoadConfigError):
        load_env.runner(batch_size=0)


def test_processing_status_keeps_its_meaning_after_a_failed_load(load_env):
    job_id = load_env.processed("market", [bar(1, source="synthetic.market")])
    with pytest.raises(LoadValidationError):
        load_env.load(job_id)
    job = load_env.job(job_id)
    assert (job.status, job.output_rows, job.load_status) == ("COMPLETED", 1, L.FAILED.value)

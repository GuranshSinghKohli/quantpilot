"""Quality checks, rejects, dedup, reprocessing, tracking and manifests with real PySpark. SYNTHETIC data only."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus
from app.pipeline.lake.keys import manifest_key, reject_key
from app.pipeline.spark.manifest import read_manifest
from app.pipeline.spark.plan import spark_path
from app.pipeline.spark.schemas import processed_column_names, processed_struct
from tests.unit.pipeline.factories import company_record, event, market_record, news_record

DAY2 = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
DAY3 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def news(n=1, **overrides):
    fields = dict(external_id=f"synthetic-news-{n}", url=f"https://example.com/synthetic/{n}", ingested_at=DAY2)
    fields.update(overrides)
    return event(news_record(**fields))


def market(symbol="SYNTH", **overrides):
    return event(market_record(symbol=symbol, ingested_at=DAY2, **overrides))


def set_record(**changes):
    def mutate(doc):
        doc["record"].update(changes)
    return mutate


def rejected(env, result, domain="news"):
    """{error_code: reject document} for a job's reject objects."""
    docs = [env.json(key) for key in env.keys(f"rejects/{domain}/")]
    return {doc["error_code"]: doc for doc in docs if doc["processing_job_id"] == str(result.job_id)}


# --- schemas ------------------------------------------------------------------------------


def test_processed_parquet_has_the_explicit_schema(env, run, spark):
    env.land(news(), market(), event(company_record(ingested_at=DAY2)))
    for domain in ("news", "market", "company"):
        run(env, env.plan(domain))
        df = spark.read.parquet(spark_path(env.store, f"processed/{domain}"))
        expected = {f.name: f.dataType.simpleString() for f in processed_struct(domain).fields}
        actual = {f.name: f.dataType.simpleString() for f in df.schema.fields}
        assert actual == {**expected, "year": "string", "month": "string", "day": "string"}
        assert [f.name for f in df.schema.fields][: len(expected)] == processed_column_names(domain)


def test_unknown_fields_are_ignored_not_inferred(env, run, read):
    ev = news()
    env.land_document(ev, lambda doc: doc.update(surprise={"nested": 1}) or doc["record"].update(extra="x"))
    result = run(env, env.plan("news"))
    assert result.counts.valid_rows == 1
    assert "surprise" not in read(env, "processed/news/")[0]


# --- malformed and invalid data -------------------------------------------------------------


def test_malformed_objects_are_rejected_not_dropped(env, run, read):
    good = news(1)
    env.land(good)
    corrupt = env.land_bytes(env.raw_key("news", date(2026, 9, 2), "a" * 64), b'{"schema_version": 1, "event_id": ')
    array = env.land_bytes(env.raw_key("news", date(2026, 9, 2), "b" * 64), b"[{\"a\": 1}, {\"a\": 2}]")
    empty = env.land_bytes(env.raw_key("news", date(2026, 9, 2), "c" * 64), b"")
    before = env.snapshot()

    result = run(env, env.plan("news"))

    assert result.status is ProcessingJobStatus.COMPLETED
    c = result.counts
    assert (c.input_objects, c.valid_rows, c.rejected_rows, c.output_rows) == (4, 1, 3, 1)
    docs = rejected(env, result)
    assert set(docs) == {"malformed_json", "multiple_documents", "unreadable_object"}
    assert docs["malformed_json"]["raw_key"] == corrupt
    assert docs["multiple_documents"]["raw_key"] == array
    assert docs["unreadable_object"]["raw_key"] == empty
    assert [r["event_id"] for r in read(env, "processed/news/")] == [good.event_id]
    assert env.snapshot() == before  # raw untouched


@pytest.mark.parametrize("mutate, code", [
    (set_record(published_at="2026-09-01 09:00:00"), "invalid_timestamp"),  # no offset
    (set_record(published_at="2099-01-01T00:00:00Z"), "timestamp_out_of_range"),
    (set_record(url="ftp://example.com/x"), "invalid_url"),
    (set_record(title="   "), "missing_required_field"),
    (set_record(tickers=["OK", "not a symbol!"]), "invalid_symbol"),
    (lambda doc: doc.update(ingested_at="yesterday"), "invalid_timestamp"),
    (lambda doc: doc.update(schema_version=99), "unsupported_schema_version"),
    (lambda doc: doc.update(domain="market"), "domain_mismatch"),
    (lambda doc: doc.update(source="massive.market_daily"), "unknown_source"),
    (lambda doc: doc.update(source_type="market_data"), "source_type_mismatch"),
    (lambda doc: doc.update(external_id="other"), "identity_mismatch"),
    (lambda doc: doc.update(event_id="d" * 64), "event_id_key_mismatch"),
    (lambda doc: doc.pop("record"), "missing_required_field"),
])
def test_news_quality_checks(env, run, mutate, code):
    ev = news()
    env.land_document(ev, mutate)
    result = run(env, env.plan("news"))
    assert (result.counts.valid_rows, result.counts.rejected_rows) == (0, 1)
    doc = rejected(env, result)[code]
    assert doc["event_id"] == ev.event_id and doc["domain"] == "news"
    assert doc["error_message"]


@pytest.mark.parametrize("changes, code", [
    (dict(open="abc"), "invalid_numeric"),
    (dict(close="1e5"), "invalid_numeric"),
    (dict(volume="-5"), "invalid_volume"),
    (dict(volume="1e5"), "invalid_volume"),
    (dict(low="0"), "non_positive_price"),
    (dict(adj_close="-1"), "non_positive_price"),
    (dict(high="90.0"), "high_below_low"),
    (dict(open="120.0"), "ohlc_inconsistent"),
    (dict(close="94.0"), "ohlc_inconsistent"),
    (dict(trading_date="2026/09/01"), "invalid_timestamp"),
    (dict(trading_date="2026-08-20"), "trading_date_mismatch"),
    (dict(interval="1h"), "invalid_interval"),
    (dict(symbol="bad symbol"), "invalid_symbol"),
    (dict(currency="usd"), "invalid_currency"),
    (dict(symbol="OTHER"), "identity_mismatch"),
])
def test_market_constraints(env, run, changes, code):
    env.land_document(market(), set_record(**changes))
    result = run(env, env.plan("market"))
    assert (result.counts.valid_rows, result.counts.rejected_rows) == (0, 1)
    assert set(rejected(env, result, "market")) == {code}


def test_market_missing_adj_close_and_currency_stay_null(env, run, read):
    env.land(market(adj_close=None, currency=None))
    run(env, env.plan("market"))
    (row,) = read(env, "processed/market/")
    assert row["adj_close"] is None and row["currency"] is None


@pytest.mark.parametrize("changes, code", [
    (dict(cik="12345"), "invalid_cik"),
    (dict(cik="00000001234"), "invalid_cik"),
    (dict(cik="0000000999"), "identity_mismatch"),
    (dict(ticker="SYNB"), "ticker_mismatch"),
    (dict(name=""), "missing_required_field"),
])
def test_company_checks(env, run, changes, code):
    env.land_document(event(company_record(ingested_at=DAY2)), set_record(**changes))
    result = run(env, env.plan("company"))
    assert set(rejected(env, result, "company")) == {code}


def test_company_keeps_cik_identity_and_every_ticker(env, run, read):
    a = event(company_record(external_id="1", cik="1", name="Same Name Inc.", ticker="AAA", tickers=["AAA", "AAB"], ingested_at=DAY2))
    b = event(company_record(external_id="2", cik="2", name="Same Name Inc.", ticker=None, tickers=[], exchange=None, ingested_at=DAY2))
    env.land(a, b)
    run(env, env.plan("company"))
    rows = {r["cik"]: r for r in read(env, "processed/company/")}
    assert set(rows) == {"0000000001", "0000000002"}  # same name, two companies
    assert rows["0000000001"]["tickers"] == ["AAA", "AAB"]
    assert (rows["0000000002"]["ticker"], rows["0000000002"]["tickers"], rows["0000000002"]["exchange"]) == (None, [], None)


# --- reject objects --------------------------------------------------------------------------


def test_reject_object_layout_and_write_once(env, run):
    ev = news()
    raw = env.land_document(ev, set_record(url="not a url"))
    first = run(env, env.plan("news"))
    key = reject_key("news", date(2026, 9, 2), ev.event_id)
    assert env.keys("rejects/") == [key]
    doc = env.json(key)
    assert {k: doc[k] for k in ("event_id", "source", "domain", "processing_job_id", "error_code", "raw_key", "raw_uri")} == {
        "event_id": ev.event_id, "source": "massive.news", "domain": "news", "processing_job_id": str(first.job_id),
        "error_code": "invalid_url", "raw_key": raw, "raw_uri": env.store.uri(raw)}
    assert doc["processed_at"] and doc["check"] and doc["error_message"]
    original = env.store.get(key)

    second = run(env, env.plan("news"))
    assert (second.rejects.written, second.rejects.already_recorded) == (0, 1)
    assert env.store.get(key) == original  # never overwritten


# --- dedup, partitions, reprocessing ---------------------------------------------------------


def test_duplicate_event_in_two_objects_is_kept_once(env, run, read):
    ev = news()
    (key,) = env.land(ev)
    copy = key.replace("day=02", "day=03")
    env.land_bytes(copy, env.store.get(key))
    result = run(env, env.plan("news", end=date(2026, 9, 3)))
    c = result.counts
    assert (c.valid_rows, c.duplicate_rows, c.output_rows) == (2, 1, 1)
    assert read(env, "processed/news/")[0]["raw_key"] == key  # lowest raw_key wins


def test_newer_version_supersedes_older_one_not_by_title(env, run, read):
    v1 = news(1, title="Same title", ingested_at=DAY2)
    v2 = news(1, title="Same title, corrected", ingested_at=DAY2 + timedelta(hours=1))
    other = news(2, title="Same title", ingested_at=DAY2)  # same title, different article: kept
    env.land(v1, v2, other)
    result = run(env, env.plan("news"))
    assert (result.counts.superseded_rows, result.counts.output_rows) == (1, 2)
    assert {r["event_id"] for r in read(env, "processed/news/")} == {v2.event_id, other.event_id}


def test_news_versions_collapse_on_the_contract_dedup_key(env, run, read):
    a = news(1, url="https://example.com/story?utm_source=feed")
    b = news(2, url="https://example.com/story", ingested_at=DAY2 + timedelta(minutes=5))  # same article, new id
    assert a.dedup_key == b.dedup_key and a.external_id != b.external_id
    env.land(a, b)
    result = run(env, env.plan("news"))
    assert (result.counts.superseded_rows, [r["event_id"] for r in read(env, "processed/news/")]) == (1, [b.event_id])


def test_market_keeps_symbol_trading_date_per_source(env, run, read):
    a = market("SYNTH")
    b = event(market_record(source="synthetic.market", symbol="SYNTH", ingested_at=DAY2))
    c = market("OTHR")
    env.land(a, b, c)
    run(env, env.plan("market"))
    rows = read(env, "processed/market/")
    assert sorted((r["source"], r["symbol"]) for r in rows) == [
        ("massive.market_daily", "OTHR"), ("massive.market_daily", "SYNTH"), ("synthetic.market", "SYNTH")]
    assert [r["synthetic"] for r in rows if r["source"] == "synthetic.market"] == [True]


def test_partition_layout_one_file_per_partition(env, run):
    env.land(news(1, published_at=datetime(2026, 9, 1, 23, 30, tzinfo=timezone.utc)),
             news(2, published_at=datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)),
             news(3, published_at=datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)))
    result = run(env, env.plan("news"))
    assert result.output_partitions == ["year=2026/month=09/day=01", "year=2026/month=09/day=02"]
    files = [k for k in env.keys("processed/news/") if k.endswith(".parquet")]
    assert [k.rsplit("/", 1)[0] for k in files] == [
        "processed/news/year=2026/month=09/day=01", "processed/news/year=2026/month=09/day=02"]
    assert all(k.rsplit("/", 1)[1].startswith("part-") for k in files)


def test_reprocessing_is_deterministic(env, run, read):
    events = [news(n) for n in range(1, 4)]
    env.land(*events)
    env.land_document(news(9), set_record(url="bad"))
    before = env.snapshot()
    first = run(env, env.plan("news"))
    rows_first = read(env, "processed/news/")

    second = run(env, env.plan("news"))
    rows_second = read(env, "processed/news/")

    assert rows_second == rows_first  # identical rows, including the first job's lineage
    assert {r["processing_job_id"] for r in rows_second} == {str(first.job_id)}
    c = second.counts
    assert (c.valid_rows, c.already_processed_rows, c.existing_rows, c.output_rows, c.new_rows) == (3, 3, 3, 3, 0)
    assert second.rejects.already_recorded == 1
    assert env.snapshot() == before


def test_incremental_run_merges_into_existing_partition(env, run, read):
    v1 = news(1, ingested_at=DAY2)
    env.land(v1, news(2, ingested_at=DAY2))
    first = run(env, env.plan("news"))
    v1b = news(1, title="Updated headline", ingested_at=DAY3)
    env.land(v1b)

    second = run(env, env.plan("news", start=date(2026, 9, 3)))

    c = second.counts
    assert (c.valid_rows, c.existing_rows, c.superseded_rows, c.output_rows, c.new_rows) == (1, 2, 1, 2, 1)
    rows = {r["external_id"]: r for r in read(env, "processed/news/")}
    assert rows["synthetic-news-1"]["event_id"] == v1b.event_id
    assert rows["synthetic-news-1"]["processing_job_id"] == str(second.job_id)
    assert rows["synthetic-news-2"]["processing_job_id"] == str(first.job_id)


def test_multiple_days_and_sources(env, run, read):
    env.land(market("SYNTH"), event(market_record(source="synthetic.market", ingested_at=DAY3,
                                                  timestamp=datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc),
                                                  trading_date=date(2026, 9, 2))))
    result = run(env, env.plan("market", end=date(2026, 9, 3)))
    assert result.counts.input_objects == 2
    assert result.output_partitions == ["year=2026/month=09/day=01", "year=2026/month=09/day=02"]
    assert {r["source"] for r in read(env, "processed/market/")} == {"massive.market_daily", "synthetic.market"}


# --- tracking, manifest, lineage -------------------------------------------------------------


def test_job_tracking_manifest_and_record_lineage(env, run):
    good = news(1)
    env.land(good, news(2))
    env.land_document(news(3), set_record(url="bad"))
    result = run(env, env.plan("news"))

    job = env.job(result.job_id)
    assert job.status == ProcessingJobStatus.COMPLETED.value
    assert job.job_name == "spark.process.news" and job.mode == "incremental"
    assert (job.input_objects, job.input_rows, job.rejected_rows, job.duplicates_removed, job.output_rows) == (3, 3, 1, 0, 2)
    assert job.output_partitions == ["year=2026/month=09/day=01"]
    assert job.output_uri == env.store.uri("processed/news")
    assert job.manifest_uri == env.store.uri(manifest_key(result.job_id))
    assert job.finished_at and job.duration_ms is not None and job.error is None
    assert job.engine_version.startswith("spark ") and job.code_version == "test"

    manifest = read_manifest(env.store, result.job_id)
    assert manifest["status"] == "COMPLETED" and manifest["domain"] == "news"
    assert manifest["input"]["objects"] == list(env.plan("news").keys)
    assert manifest["counts"]["output_rows"] == 2 and manifest["rejects"]["by_code"] == {"invalid_url": 1}
    assert [f["key"] for f in manifest["output"]["files"]] == [k for k in env.keys("processed/news/") if k.endswith(".parquet")]
    assert manifest["engine"]["app_id"] == job.spark_app_id
    assert manifest["schema"]["processed_schema_version"] == 1

    records = env.records()
    assert records[good.event_id].status == IngestionRecordStatus.PROCESSED.value
    assert records[good.event_id].processing_job_id == result.job_id


def test_empty_input_completes_without_output(env, run):
    result = run(env, env.plan("news"))
    assert result.status is ProcessingJobStatus.COMPLETED
    assert result.counts.as_dict()["output_rows"] == 0 and result.output_partitions == []
    assert env.keys("processed/") == [] and env.keys("rejects/") == []
    assert read_manifest(env.store, result.job_id)["input"]["objects"] == []
    assert env.job(result.job_id).spark_app_id is None  # Spark never ran


def test_spark_failure_is_recorded_as_failed(env, run):
    (key,) = env.land(news())
    plan = env.plan("news")
    os.remove(env.store.root / key)  # vanishes between planning and reading
    with pytest.raises(Exception, match="PATH_NOT_FOUND"):
        run(env, plan)
    (job,) = env.jobs()
    assert job.status == ProcessingJobStatus.FAILED.value
    assert "PATH_NOT_FOUND" in job.error and job.finished_at
    manifest = read_manifest(env.store, job.id)
    assert manifest["status"] == "FAILED" and "PATH_NOT_FOUND" in manifest["error"]
    assert env.keys("processed/") == []

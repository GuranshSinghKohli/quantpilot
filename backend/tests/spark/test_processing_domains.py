"""Raw → processed for each domain with real PySpark: typed columns, lineage, partitions, tracking."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus
from app.pipeline.spark.schemas import processed_column_names
from tests.unit.pipeline.factories import company_record, event, market_record, news_record
from tests.unit.pipeline.processing_env import TODAY

DAY = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _lineage_ok(env, row, ev, result):
    assert row["event_id"] == ev.event_id
    assert row["source"] == ev.source
    assert row["content_hash"] == ev.content_hash
    assert row["processing_job_id"] == str(result.job_id)
    assert row["raw_key"].startswith(f"raw/{ev.domain}/year=2026/month=09/day=02/")
    assert row["raw_uri"] == env.store.uri(row["raw_key"])


def test_news_raw_to_processed(env, run, read):
    ev = event(news_record(title="  Synthetic   headline\n for tests ", ingested_at=DAY))
    env.land(ev)
    before = env.snapshot()
    result = run(env, env.plan("news"))

    assert result.status is ProcessingJobStatus.COMPLETED
    assert (result.counts.valid_rows, result.counts.rejected_rows, result.counts.output_rows) == (1, 0, 1)
    rows = read(env, "processed/news/")
    assert len(rows) == 1
    row = rows[0]
    _lineage_ok(env, row, ev, result)
    assert row["title"] == "Synthetic headline for tests"
    assert row["published_at"] == datetime(2026, 9, 1, 9, 0)  # UTC wall clock (session zone is UTC)
    assert row["tickers"] == ["SYNTH"]
    assert (row["year"], row["month"], row["day"]) == ("2026", "09", "01")  # partitioned by published_at
    assert set(processed_column_names("news")) <= set(row)
    assert env.snapshot() == before
    assert env.records()[ev.event_id].status == IngestionRecordStatus.PROCESSED.value
    assert env.records()[ev.event_id].processing_job_id == result.job_id


def test_market_raw_to_processed(env, run, read):
    ev = event(market_record(ingested_at=DAY))
    env.land(ev)
    result = run(env, env.plan("market"))

    assert result.status is ProcessingJobStatus.COMPLETED
    (row,) = read(env, "processed/market/")
    _lineage_ok(env, row, ev, result)
    assert row["symbol"] == "SYNTH"
    assert row["trading_date"] == date(2026, 9, 1)
    assert (row["open"], row["high"], row["low"], row["close"]) == (Decimal("100.000000"), Decimal("110.000000"),
                                                                    Decimal("95.000000"), Decimal("105.000000"))
    assert row["volume"] == 1000
    assert row["currency"] == "USD"
    assert (row["year"], row["month"], row["day"]) == ("2026", "09", "01")


def test_company_raw_to_processed(env, run, read):
    ev = event(company_record(ingested_at=DAY))
    env.land(ev)
    result = run(env, env.plan("company"))

    assert result.status is ProcessingJobStatus.COMPLETED
    (row,) = read(env, "processed/company/")
    _lineage_ok(env, row, ev, result)
    assert row["cik"] == "0000000123"
    assert row["ticker"] == "SYNA"
    assert row["tickers"] == ["SYNA", "SYNB"]
    assert (row["year"], row["month"], row["day"]) == ("2026", "09", "02")  # snapshot date = ingested_at
    job = env.job(result.job_id)
    assert job.status == ProcessingJobStatus.COMPLETED.value
    assert (job.input_objects, job.output_rows, job.rejected_rows) == (1, 1, 0)
    assert job.spark_app_id and job.spark_app_id == result.spark_app_id
    assert job.manifest_uri == result.manifest_uri
    assert job.since == TODAY and job.until == TODAY

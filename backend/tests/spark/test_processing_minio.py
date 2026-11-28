"""End to end on MinIO through S3A: raw objects → PySpark → Parquet + rejects + manifest + processing_jobs, twice.

Runs in its own throwaway bucket (tests/s3 fixture); needs TEST_S3_ENDPOINT. SYNTHETIC data only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus
from app.pipeline.spark.manifest import read_manifest
from tests.unit.pipeline.factories import company_record, event, market_record, news_record

pytestmark = pytest.mark.s3

DAY2 = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def test_minio_end_to_end_is_deterministic(s3_env, run, read):
    env = s3_env
    news = [event(news_record(external_id=f"synthetic-news-{n}", url=f"https://example.com/{n}", ingested_at=DAY2))
            for n in range(1, 4)]
    market = [event(market_record(symbol=s, ingested_at=DAY2)) for s in ("SYNTH", "OTHR")]
    company = [event(company_record(ingested_at=DAY2))]
    env.land(*news, *market, *company)
    bad = event(market_record(symbol="BADD", ingested_at=DAY2))
    env.land_document(bad, lambda doc: doc["record"].update(high="1.0"))
    raw_before = env.snapshot()

    first = {d: run(env, env.plan(d)) for d in ("news", "market", "company")}
    rows_first = {d: read(env, f"processed/{d}/") for d in first}
    second = {d: run(env, env.plan(d)) for d in first}
    rows_second = {d: read(env, f"processed/{d}/") for d in first}

    assert all(r.status is ProcessingJobStatus.COMPLETED for r in (*first.values(), *second.values()))
    assert {d: len(rows) for d, rows in rows_first.items()} == {"news": 3, "market": 2, "company": 1}
    assert rows_second == rows_first
    assert all(r.counts.new_rows == 0 for r in second.values())

    for domain, result in first.items():
        for row in rows_first[domain]:
            assert row["raw_uri"] == f"s3://{env.store.bucket}/{row['raw_key']}"
            assert row["processing_job_id"] == str(result.job_id)
        manifest = read_manifest(env.store, result.job_id)
        assert manifest["store"]["uri_prefix"] == f"s3://{env.store.bucket}/"
        assert manifest["output"]["files"] and all(f["uri"].startswith("s3://") for f in manifest["output"]["files"])
        job = env.job(result.job_id)
        assert job.status == "COMPLETED" and job.spark_app_id and job.output_uri == f"s3://{env.store.bucket}/processed/{domain}"

    (reject,) = [env.json(k) for k in env.keys("rejects/market/")]
    assert (reject["event_id"], reject["error_code"]) == (bad.event_id, "high_below_low")
    assert second["market"].rejects.already_recorded == 1
    assert all(r.status == IngestionRecordStatus.PROCESSED.value
               for eid, r in env.records().items() if eid != bad.event_id)
    assert env.snapshot() == raw_before

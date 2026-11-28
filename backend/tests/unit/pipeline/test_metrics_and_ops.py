"""Step 2.8: metrics cardinality, status, stale jobs, reconcile, recovery."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.pipeline_context import pipeline_bind, pipeline_fields
from app.domain.enums import CanonicalLoadStatus, IngestionRecordStatus, IngestionRunStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRecord, IngestionRun, ProcessingJob
from app.pipeline.lake import LocalObjectStore
from app.pipeline.load.errors import LoadConfigError
from app.pipeline.metrics import MetricCardinalityError, MetricLabelError, Metrics
from app.pipeline.ops.check import stale_report
from app.pipeline.ops.quality import quality_summary
from app.pipeline.ops.reconcile import reconcile
from app.pipeline.ops.recover import retry_ingestion, retry_load
from app.pipeline.ops.status import pipeline_status
from tests.unit.pipeline.factories import company_record, event
from tests.unit.pipeline.load_env import LoadEnv
from tests.unit.pipeline.pipeline_env import migrated_sessions

UTC = timezone.utc


@pytest.fixture()
def env(tmp_path):
    return LoadEnv(LocalObjectStore(tmp_path / "lake"), migrated_sessions(tmp_path / "db.sqlite"))


def _company(cik="123"):
    return event(company_record(external_id=cik, cik=cik, name="Synthetic Holdings Inc.",
                                ticker="SYNA", tickers=["SYNA"], exchange="Nasdaq"))


def test_forbidden_labels_are_rejected():
    m = Metrics()
    with pytest.raises(MetricLabelError, match="event_id"):
        m.inc("ingestion_records_total", event_id="abc")
    with pytest.raises(MetricLabelError, match="ticker"):
        m.inc("x_total", ticker="AAPL")
    with pytest.raises(MetricLabelError, match="job_id"):
        m.set("pipeline_processing_jobs", 1, job_id=str(uuid.uuid4()))


def test_cardinality_cap_refuses_new_series():
    m = Metrics(max_series=4)
    for i in range(4):
        m.inc("x_total", status=str(i))
    with pytest.raises(MetricCardinalityError):
        m.inc("x_total", status="overflow")
    assert m.total("x_total") == 4


def test_prometheus_render_and_allowed_labels():
    m = Metrics()
    m.inc("ingestion_runs_total", source="sec.company_tickers", status="COMPLETED")
    m.observe("canonical_load_duration_seconds", 0.12, domain="company")
    text = m.render_prometheus()
    assert "quantpilot_ingestion_runs_total" in text
    assert "quantpilot_canonical_load_duration_seconds_sum" in text
    assert m.label_keys() == ["domain", "source", "status"]


def test_pipeline_bind_sets_correlation_id():
    with pipeline_bind(component="ingestor", operation="ingest", domain="company", run_id="r1"):
        fields = pipeline_fields()
        assert fields["component"] == "ingestor"
        assert fields["correlation_id"]
        assert fields["run_id"] == "r1"


def test_status_reports_latest_and_failed(env):
    job_id = env.processed("company", [_company()])
    env.load(job_id)
    with env.sessions() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = ProcessingJobStatus.FAILED.value
        db.flush()
        db.commit()
        report = pipeline_status(db, include_kafka=False, include_dlq=False)
    assert report["latest_processing"]["company"]["id"] == str(job_id)
    assert report["failed"]["jobs"]
    assert report["ingestion_records"]


def test_stale_running_jobs_are_reported_not_mutated(env, monkeypatch):
    monkeypatch.setenv("PIPELINE_STALE_JOB_SECONDS", "60")
    monkeypatch.setenv("PIPELINE_STALE_LOAD_SECONDS", "60")
    monkeypatch.setenv("PIPELINE_STALE_RUN_SECONDS", "60")
    job_id = env.processed("company", [_company()])
    old = datetime.now(UTC) - timedelta(hours=2)
    with env.sessions() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = ProcessingJobStatus.RUNNING.value
        job.started_at = old
        job.load_status = CanonicalLoadStatus.RUNNING.value
        job.load_started_at = old
        db.commit()
        report = stale_report(db, now=datetime.now(UTC))
        assert report["stale"] is True
        assert any(s["id"] == str(job_id) for s in report["processing_jobs"])
        assert any(s["id"] == str(job_id) for s in report["canonical_loads"])
        assert db.get(ProcessingJob, job_id).status == ProcessingJobStatus.RUNNING.value


def test_reconcile_passes_a_known_good_job(env):
    job_id = env.processed("company", [_company("111"), _company("222")])
    env.load(job_id)
    with env.sessions() as db:
        report = reconcile(db, env.store, domain="company", job_id=job_id)
    assert report["ok"] is True
    assert report["problems"] == []


def test_reconcile_detects_missing_manifest_file(env):
    job_id = env.processed("company", [_company()])
    files = __import__("json").loads(env.store.get(f"_manifests/processing/{job_id}.json"))["output"]["files"]
    env.store.root.joinpath(files[0]["key"]).unlink()
    with env.sessions() as db:
        report = reconcile(db, env.store, job_id=job_id)
    assert report["ok"] is False
    assert any("missing output" in p for p in report["problems"])


def test_reconcile_detects_broken_lineage_and_counts(env):
    job_id = env.processed("company", [_company()])
    env.load(job_id)
    with env.sessions() as db:
        db.execute(update(IngestionRecord).values(raw_uri=None))
        job = db.get(ProcessingJob, job_id)
        job.load_candidates = 99
        db.commit()
        report = reconcile(db, env.store, job_id=job_id)
    assert report["ok"] is False
    assert any("raw_uri" in p or "candidates" in p for p in report["problems"])


def test_reconcile_detects_invalid_processed_without_job(env):
    env.processed("company", [_company()])
    with env.sessions() as db:
        db.execute(update(IngestionRecord).values(processing_job_id=None,
                                                 status=IngestionRecordStatus.PROCESSED.value))
        db.commit()
        report = reconcile(db, env.store, domain="company")
    assert report["ok"] is False
    assert any("no processing_job_id" in p for p in report["problems"])


def test_retry_load_refuses_incomplete_job(env):
    job_id = env.processed("company", [_company()])
    with env.sessions() as db:
        db.get(ProcessingJob, job_id).status = ProcessingJobStatus.FAILED.value
        db.commit()
    with pytest.raises(LoadConfigError, match="FAILED"):
        retry_load(env.store, env.sessions, job_id)


def test_retry_load_replays_a_completed_job(env):
    job_id = env.processed("company", [_company()])
    env.load(job_id)
    result = retry_load(env.store, env.sessions, job_id)
    assert result.status.value == "CANONICAL_LOAD_COMPLETE"
    assert result.counts.duplicates == 1


def test_retry_ingestion_guards(env):
    env.processed("company", [_company()])
    with env.sessions() as db:
        record = db.scalars(select(IngestionRecord)).first()
        run_id = record.run_id
        run = db.get(IngestionRun, run_id)
        run.status = IngestionRunStatus.COMPLETED.value
        run.params = {"start": "2026-09-01T00:00:00+00:00", "end": "2026-09-02T00:00:00+00:00",
                      "symbols": ["AAPL"], "max_records": 1, "page_size": 1, "max_pages": 1}
        db.commit()
    with pytest.raises(LoadConfigError, match="COMPLETED"):
        retry_ingestion(env.sessions, run_id)
    with pytest.raises(LoadConfigError, match="not found"):
        retry_ingestion(env.sessions, uuid.uuid4())


def test_quality_count_chain(env):
    job_id = env.processed("company", [
        event(company_record(external_id="1", cik="1", name="One Corp", ticker="ONE", tickers=["ONE"], exchange="NYSE")),
        event(company_record(external_id="2", cik="2", name="Two Corp", ticker="TWO", tickers=["TWO"], exchange="NYSE")),
    ])
    env.load(job_id)
    with env.sessions() as db:
        summary = quality_summary(db, domain="company")
    company = summary["domains"]["company"]
    assert company["canonical_rows_now"] == 2
    assert company["canonical_inserted"] == 2
    assert company["processed"] >= 2


def test_dev_db_is_not_this_fixture_path(env):
    assert "quantpilot.db" not in str(env.sessions().get_bind().url)

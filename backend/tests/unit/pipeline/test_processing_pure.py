"""Spark processing without a JVM: config, input planning, schema specs, check codes,
and the job runner lifecycle (tracking, rejects, manifest) with a fake engine."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.core.settings import Settings
from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus
from app.pipeline.contracts import CompanyEvent, CompanyRecord, MarketEvent, MarketRecord, NewsEvent, NewsRecord
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake.keys import manifest_key, reject_key
from app.pipeline.metrics import Metrics
from app.pipeline.spark.config import SparkConfig
from app.pipeline.spark.jobs.runner import EngineResult, ProcessingCounts, ProcessingJobRunner
from app.pipeline.spark.manifest import read_manifest
from app.pipeline.spark.plan import PlanError, encode_key, spark_path, uri_prefix
from app.pipeline.spark.quality import CHECKS
from app.pipeline.spark.schemas import (
    ENVELOPE_FIELDS,
    LINEAGE_COLUMNS,
    WIRE_RECORD_FIELDS,
    processed_column_names,
)
from app.pipeline.spark.writers import RejectRow
from tests.unit.pipeline.factories import event, news_record
from tests.unit.pipeline.pipeline_env import migrated_sessions
from tests.unit.pipeline.processing_env import ProcessingEnv

BACKEND = Path(__file__).resolve().parents[3]
SPARK_DIR = BACKEND / "app" / "pipeline" / "spark"


@pytest.fixture()
def env(tmp_path):
    return ProcessingEnv(LocalObjectStore(tmp_path / "lake"), migrated_sessions(tmp_path / "db.sqlite"))


def news(n=1, **kw):
    return event(news_record(external_id=f"synthetic-news-{n}", url=f"https://example.com/{n}", **kw))


# --- config ---------------------------------------------------------------------------------


def _config(**kw):
    fields = dict(app_name="qp", master="local[*]", shuffle_partitions=8, log_level="WARN", driver_memory="2g",
                  max_records_per_file=1000, lake_backend="local")
    fields.update(kw)
    return SparkConfig(**fields)


def test_spark_conf_is_utc_and_s3a_only_for_s3():
    conf = _config().conf()
    assert conf["spark.sql.session.timeZone"] == "UTC"
    assert conf["spark.sql.sources.partitionOverwriteMode"] == "dynamic"
    assert conf["spark.ui.enabled"] == "false"
    assert not any(k.startswith("spark.hadoop.fs.s3a") for k in conf)

    s3 = _config(lake_backend="s3", endpoint_url="http://minio:9000").conf()
    assert s3["spark.hadoop.fs.s3a.endpoint"] == "http://minio:9000"
    assert s3["spark.hadoop.fs.s3a.path.style.access"] == "true"
    assert s3["spark.hadoop.fs.s3a.connection.ssl.enabled"] == "false"
    assert _config(lake_backend="s3", endpoint_url="https://s3.example").conf()["spark.hadoop.fs.s3a.connection.ssl.enabled"] == "true"
    assert "spark.hadoop.fs.s3a.endpoint" not in _config(lake_backend="s3").conf()  # real AWS: default endpoint


def test_spark_conf_never_carries_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-NOT-REAL")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-a-real-secret")
    conf = SparkConfig.from_settings(Settings(_env_file=None, LAKE_BACKEND="s3", LAKE_ENDPOINT_URL="http://minio:9000")).conf()
    text = json.dumps(conf).lower()
    assert "not-a-real-secret" not in text and "akia-not-real" not in text
    assert not any("secret" in k or "access.key" in k or "session.token" in k for k in conf)


@pytest.mark.parametrize("field, value", [
    ("SPARK_MASTER", "local[0]"), ("SPARK_MASTER", "mesos://x"), ("SPARK_MASTER", "local[*]; rm -rf"),
    ("SPARK_DRIVER_MEMORY", "lots"), ("SPARK_SHUFFLE_PARTITIONS", 0), ("SPARK_LOG_LEVEL", "TRACE"),
    ("SPARK_APP_NAME", "has spaces and $"),
])
def test_invalid_spark_settings_are_rejected(field, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_default_spark_settings():
    s = Settings(_env_file=None)
    assert (s.SPARK_MASTER, s.SPARK_SHUFFLE_PARTITIONS, s.SPARK_LOG_LEVEL) == ("local[*]", 8, "WARN")
    assert Settings(_env_file=None, SPARK_MASTER="local[4]", SPARK_DRIVER_MEMORY="4G").SPARK_DRIVER_MEMORY == "4g"


# --- input planning -------------------------------------------------------------------------


class ListSpy(LocalObjectStore):
    def __init__(self, root):
        super().__init__(root)
        self.listed = []

    def list(self, prefix=""):
        self.listed.append(prefix)
        return super().list(prefix)


def test_window_lists_only_the_requested_days(tmp_path):
    env = ProcessingEnv(ListSpy(tmp_path / "lake"), migrated_sessions(tmp_path / "db.sqlite"))
    env.land(news(1, ingested_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc)),
             news(2, ingested_at=datetime(2026, 9, 2, 12, tzinfo=timezone.utc)), track=False)
    plan = env.plan("news", start=date(2026, 9, 2))
    assert env.store.listed == ["raw/news/year=2026/month=09/day=02/"]
    assert len(plan.keys) == 1 and (plan.mode, plan.since, plan.until) == ("window", date(2026, 9, 2), date(2026, 9, 2))


def test_window_defaults_to_today(env):
    plan = env.plan("news", start=None, today=date(2026, 9, 5))
    assert (plan.since, plan.until, plan.keys) == (date(2026, 9, 5), date(2026, 9, 5), ())


def test_non_raw_objects_are_ignored_and_reported(env):
    (key,) = env.land(news(), track=False)
    env.land_bytes("raw/news/year=2026/month=09/day=02/notes.txt", b"x")
    plan = env.plan("news")
    assert plan.keys == (key,) and plan.ignored == ("raw/news/year=2026/month=09/day=02/notes.txt",)


@pytest.mark.parametrize("kwargs, message", [
    (dict(start=date(2026, 9, 3), end=date(2026, 9, 1)), "after end"),
    (dict(start=date(2020, 1, 1), end=date(2026, 9, 1)), "PROCESS_MAX_WINDOW_DAYS"),
    (dict(prefix="raw/market/"), "must start with"),
    (dict(prefix="processed/news/"), "must start with"),
    (dict(objects=["raw/news/year=2026/month=09/day=02/" + "0" * 64 + ".json"]), "no object"),
    (dict(objects=["raw/market/year=2026/month=09/day=02/" + "0" * 64 + ".json"]), "not news"),
    (dict(objects=["processed/news/x.json"]), ""),
    (dict(output_prefix="raw/news/"), "under processed/"),
    (dict(output_prefix="processed/"), "under processed/"),
    (dict(output_prefix="processed/news"), "ending in"),
    (dict(output_prefix="processed/../raw/"), ""),
])
def test_bad_selections_are_config_errors(env, kwargs, message):
    with pytest.raises(PlanError, match=message):
        env.plan("news", **kwargs)


def test_selection_modes_are_exclusive(env):
    from app.pipeline.spark.plan import ProcessingRequest, plan_inputs

    with pytest.raises(PlanError, match="choose one"):
        plan_inputs(env.store, ProcessingRequest("news", start=date(2026, 9, 2), prefix="raw/news/"),
                    max_window_days=10, max_objects=10)


def test_object_cap(env):
    env.land(news(1), news(2), news(3), track=False)
    with pytest.raises(PlanError, match="PROCESS_MAX_INPUT_OBJECTS=2"):
        env.plan("news", max_objects=2)


def test_prefix_and_explicit_objects(env):
    keys = env.land(news(1), news(2), track=False)
    assert env.plan("news", prefix="raw/news/year=2026/").keys == tuple(sorted(keys))
    assert env.plan("news", objects=[keys[1], keys[0], keys[1]]).keys == tuple(sorted(keys))


def test_local_uris_match_the_store(env):
    key = "raw/news/year=2026/month=09/day=02/" + "a" * 64 + ".json"
    prefix, encode = uri_prefix(env.store)
    assert prefix + (encode_key(key) if encode else key) == env.store.uri(key)
    assert spark_path(env.store, key) == "file://" + (env.store.root / key).as_posix()  # unescaped for Hadoop


# --- schema specs and checks ----------------------------------------------------------------


@pytest.mark.parametrize("domain, model", [("news", NewsRecord), ("market", MarketRecord), ("company", CompanyRecord)])
def test_wire_schema_matches_the_contract(domain, model):
    assert set(WIRE_RECORD_FIELDS[domain]) == set(model.model_fields)


@pytest.mark.parametrize("model", [NewsEvent, MarketEvent, CompanyEvent])
def test_envelope_fields_match_the_contract(model):
    assert set(ENVELOPE_FIELDS) | {"record", "original"} == set(model.model_fields)


def test_processed_columns_carry_lineage():
    lineage = [c.name for c in LINEAGE_COLUMNS]
    for domain in ("news", "market", "company"):
        names = processed_column_names(domain)
        assert names[: len(lineage)] == lineage and len(set(names)) == len(names)
    assert {"event_id", "raw_uri", "processing_job_id", "source", "content_hash"} <= set(lineage)


def test_every_rule_code_is_documented():
    codes = set()
    for path in SPARK_DIR.rglob("*.py"):
        text = path.read_text()
        import re

        codes |= set(re.findall(r'Rule\(\s*"([a-z_]+)"', text))
        codes |= set(re.findall(r'F\.lit\("([a-z_]+)"\)\.alias\("code"\)', text))
    codes.add("missing_required_field")
    assert codes and codes <= set(CHECKS), codes - set(CHECKS)


def test_spark_modules_use_no_python_udfs_or_inference():
    for path in SPARK_DIR.rglob("*.py"):
        text = path.read_text()
        for banned in ("@udf", "F.udf", "pandas_udf", "inferSchema", "samplingRatio", ".rdd.", "collect()[", "applyInPandas"):
            assert banned not in text, f"{banned} in {path}"


# --- runner lifecycle -----------------------------------------------------------------------


class FakeEngine:
    name = "fake"
    version = "0"
    master = "none"

    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def process(self, plan, job):
        self.calls.append((plan, job))
        return self.respond(plan, job)


def _ok(env, *, rejects=(), new=None):
    def respond(plan, job):
        valid = len(plan.keys) - len(rejects)
        counts = ProcessingCounts(input_objects=len(plan.keys), valid_rows=valid, rejected_rows=len(rejects),
                                  output_rows=valid, new_rows=valid)
        env.land_bytes("processed/news/year=2026/month=09/day=01/part-00000-x.c000.snappy.parquet", b"PAR1")
        ids = new if new is not None else [k.rsplit("/", 1)[1][:-5] for k in plan.keys if k not in {r.raw_key for r in rejects}]
        return EngineResult(counts, ["year=2026/month=09/day=01"], rejects, {r.code: 1 for r in rejects}, ids, "app-1")
    return respond


def _runner(env, engine, **kw):
    return ProcessingJobRunner(store=env.store, session_factory=env.sessions, engine=engine, code_version="abc123", **kw)


def test_completed_job_tracks_everything(env):
    good, bad = news(1), news(2)
    _, bad_key = env.land(good, bad)
    rejects = [RejectRow(bad_key, "invalid_url", "url must be ...", "massive.news", None)]
    metrics = Metrics()
    result = _runner(env, FakeEngine(_ok(env, rejects=rejects)), metrics=metrics).run(env.plan("news"))

    assert result.status is ProcessingJobStatus.COMPLETED
    job = env.job(result.job_id)
    assert (job.status, job.input_objects, job.rejected_rows, job.output_rows) == ("COMPLETED", 2, 1, 1)
    assert (job.spark_app_id, job.engine_version, job.code_version) == ("app-1", "fake 0", "abc123")
    assert job.manifest_uri == env.store.uri(manifest_key(result.job_id))
    manifest = read_manifest(env.store, result.job_id)
    assert manifest["status"] == "COMPLETED" and manifest["rejects"]["by_code"] == {"invalid_url": 1}
    assert [f["key"] for f in manifest["output"]["files"]] == ["processed/news/year=2026/month=09/day=01/part-00000-x.c000.snappy.parquet"]
    reject = env.json(reject_key("news", date(2026, 9, 2), bad.event_id))
    assert reject["processing_job_id"] == str(result.job_id) and reject["raw_uri"] == env.store.uri(bad_key)
    records = env.records()
    assert records[good.event_id].status == IngestionRecordStatus.PROCESSED.value
    assert records[good.event_id].processing_job_id == result.job_id
    assert records[bad.event_id].status == IngestionRecordStatus.LANDED.value  # rejects stay landed
    assert metrics.snapshot()


def test_empty_input_never_calls_the_engine(env):
    engine = FakeEngine(lambda plan, job: pytest.fail("engine called"))
    result = _runner(env, engine).run(env.plan("news"))
    assert result.status is ProcessingJobStatus.COMPLETED and engine.calls == []
    assert env.job(result.job_id).output_rows == 0


def test_engine_failure_is_failed_not_completed(env):
    env.land(news())

    def boom(plan, job):
        raise RuntimeError("executor lost")

    with pytest.raises(RuntimeError, match="executor lost"):
        _runner(env, FakeEngine(boom)).run(env.plan("news"))
    (job,) = env.jobs()
    assert job.status == "FAILED" and "executor lost" in job.error and job.finished_at is not None
    assert read_manifest(env.store, job.id)["status"] == "FAILED"
    assert all(r.status == IngestionRecordStatus.LANDED.value for r in env.records().values())


def test_counter_invariant_violation_fails_the_job(env):
    env.land(news())

    def lying(plan, job):
        return EngineResult(ProcessingCounts(valid_rows=1, output_rows=5, new_rows=1), [], (), {}, [], "app-2")

    with pytest.raises(RuntimeError, match="invariants violated"):
        _runner(env, FakeEngine(lying)).run(env.plan("news"))
    (job,) = env.jobs()
    assert job.status == "FAILED" and job.spark_app_id == "app-2"
    assert all(r.status == IngestionRecordStatus.LANDED.value for r in env.records().values())


def test_release_runs_even_when_reject_writing_fails(env, monkeypatch):
    env.land(news())
    released = []

    def respond(plan, job):
        result = _ok(env, rejects=[RejectRow("not-a-raw-key", "invalid_url", "m")])(plan, job)
        result.release = lambda: released.append(True)
        return result

    with pytest.raises(Exception):
        _runner(env, FakeEngine(respond)).run(env.plan("news"))
    assert released == [True] and env.jobs()[0].status == "FAILED"


def test_manifest_is_write_once(env):
    from app.pipeline.lake import IntegrityError
    from app.pipeline.spark.manifest import write_manifest

    job_id = uuid.uuid4()
    write_manifest(env.store, job_id, {"a": 1})
    with pytest.raises(IntegrityError):
        write_manifest(env.store, job_id, {"a": 2})
    assert read_manifest(env.store, job_id) == {"a": 1}


def test_counts_invariants():
    ok = ProcessingCounts(input_objects=5, valid_rows=4, rejected_rows=1, duplicate_rows=1, already_processed_rows=1,
                          existing_rows=3, superseded_rows=1, output_rows=4, new_rows=1)
    assert ok.violations() == [] and ok.duplicates_removed == 3
    assert ProcessingCounts(input_objects=2, valid_rows=1).violations()


# --- isolation from the API -----------------------------------------------------------------


def test_api_import_never_loads_spark():
    code = ("import sys, app.main; "
            "bad = [m for m in sys.modules if m == 'pyspark' or m.startswith('pyspark.') or m.startswith('app.pipeline.spark')]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", "ENVIRONMENT": "test", "DATABASE_URL": "sqlite://"})
    assert done.returncode == 0, done.stdout + done.stderr


def test_process_cli_config_errors_exit_2(tmp_path):
    base = {"PATH": "/usr/bin:/bin", "ENVIRONMENT": "test", "DATABASE_URL": f"sqlite:///{tmp_path}/none.db",
            "LAKE_BACKEND": "local", "LAKE_LOCAL_ROOT": str(tmp_path / "lake")}
    for args in (["news", "--start", "2026-09-03", "--end", "2026-09-01"], ["all", "--prefix", "raw/news/"],
                 ["news", "--start", "not-a-date"], ["news", "--output-prefix", "raw/"]):
        done = subprocess.run([sys.executable, "-m", "app.pipeline", "process", *args], cwd=BACKEND,
                              capture_output=True, text=True, timeout=120, env=base)
        assert done.returncode == 2, (args, done.stdout, done.stderr)
        assert "Traceback" not in done.stderr

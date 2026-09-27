"""Spark tier: real PySpark (local[2]) against a tmp local lake, and against MinIO
through S3A when TEST_S3_ENDPOINT is set. Skipped without pyspark + Java; run it
with ``make test-spark`` (the spark-test image). SYNTHETIC data only.

One SparkSession serves the whole tier. When TEST_S3_ENDPOINT is set, the
AWS_* variables S3A reads are set from TEST_S3_* before that session starts.
"""

from __future__ import annotations

import importlib.util
import os
import shutil

import pytest

from app.pipeline.lake import LocalObjectStore
from tests.s3.conftest import s3_client, s3_store  # noqa: F401  (fixtures for the MinIO tests)
from tests.unit.pipeline.pipeline_env import migrated_sessions
from tests.unit.pipeline.processing_env import ProcessingEnv

HERE = os.path.dirname(__file__)
TEST_S3_ENDPOINT = os.getenv("TEST_S3_ENDPOINT", "").strip()


def _missing() -> str:
    if importlib.util.find_spec("pyspark") is None:
        return "pyspark not installed (use the spark-test image: make test-spark)"
    java_home = os.getenv("JAVA_HOME")
    if not (shutil.which("java") or (java_home and os.path.exists(os.path.join(java_home, "bin", "java")))):
        return "no Java runtime (use the spark-test image: make test-spark)"
    return ""


MISSING = _missing()


def pytest_collection_modifyitems(config, items):
    for item in items:
        if str(item.fspath).startswith(HERE):
            item.add_marker(pytest.mark.spark)
            if MISSING:
                item.add_marker(pytest.mark.skip(reason=MISSING))
            elif item.get_closest_marker("s3") and not TEST_S3_ENDPOINT:
                item.add_marker(pytest.mark.skip(reason="TEST_S3_ENDPOINT not set"))


def spark_config(**overrides):
    from app.pipeline.spark.config import SparkConfig

    fields = dict(app_name="quantpilot-test", master="local[2]", shuffle_partitions=2, log_level="ERROR",
                  driver_memory="1g", max_records_per_file=1_000_000,
                  lake_backend="s3" if TEST_S3_ENDPOINT else "local", endpoint_url=TEST_S3_ENDPOINT)
    fields.update(overrides)
    return SparkConfig(**fields)


@pytest.fixture(scope="session")
def spark():
    if TEST_S3_ENDPOINT:
        for target, source in (("AWS_ACCESS_KEY_ID", "TEST_S3_ACCESS_KEY_ID"),
                               ("AWS_SECRET_ACCESS_KEY", "TEST_S3_SECRET_ACCESS_KEY")):
            if os.getenv(source):
                os.environ[target] = os.environ[source]
    from app.pipeline.spark.session import build_session

    session = build_session(spark_config())
    yield session
    session.stop()


@pytest.fixture()
def env(tmp_path):
    return ProcessingEnv(LocalObjectStore(tmp_path / "lake"), migrated_sessions(tmp_path / "db.sqlite"))


@pytest.fixture()
def s3_env(tmp_path, s3_store):  # noqa: F811  (pytest injects the imported fixture)
    return ProcessingEnv(s3_store, migrated_sessions(tmp_path / "db.sqlite"))


@pytest.fixture()
def run(spark):
    """``run(env, plan)`` → JobResult, with the shared session and a fixed clock-free runner."""
    from app.pipeline.metrics import Metrics
    from app.pipeline.spark.jobs.engine import SparkEngine
    from app.pipeline.spark.jobs.runner import ProcessingJobRunner

    def _run(env: ProcessingEnv, plan, **kwargs):
        engine = SparkEngine(env.store, spark_config(), session=spark)
        runner = ProcessingJobRunner(store=env.store, session_factory=env.sessions, engine=engine,
                                     metrics=kwargs.pop("metrics", Metrics()), code_version="test", **kwargs)
        return runner.run(plan)

    return _run


@pytest.fixture()
def read(spark):
    """Read processed Parquet back: ``read(env, "processed/news/")`` → list of Row dicts sorted by event_id."""
    from app.pipeline.spark.plan import spark_path

    def _read(env: ProcessingEnv, prefix: str):
        if not env.keys(prefix):
            return []
        df = spark.read.parquet(spark_path(env.store, prefix.rstrip("/")))
        return sorted((row.asDict(recursive=True) for row in df.collect()), key=lambda r: r["event_id"])

    return _read

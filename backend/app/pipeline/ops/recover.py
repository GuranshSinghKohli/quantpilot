"""Guarded recovery: verify prerequisites, then call the existing runners."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.domain.enums import IngestionRunStatus, ProcessingJobStatus
from app.domain.ingestion_models import IngestionRun, ProcessingJob
from app.pipeline.lake import ObjectStore
from app.pipeline.lake.keys import manifest_key
from app.pipeline.load import CanonicalLoadRunner, LoadConfigError
from app.pipeline.spark.manifest import MANIFEST_VERSION
from app.pipeline.spark.schemas import PROCESSED_SCHEMA_VERSION


def retry_load(store: ObjectStore, session_factory: Callable[[], Session], job_id: uuid.UUID):
    """Re-run the existing loader after checking the job and its manifest."""
    with session_factory() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise LoadConfigError(f"processing job {job_id} not found")
        if job.status != ProcessingJobStatus.COMPLETED.value:
            raise LoadConfigError(f"processing job {job_id} is {job.status}; only COMPLETED jobs can be loaded")
        _require_manifest(store, job)
    return CanonicalLoadRunner(store=store, session_factory=session_factory).run(job_id)


def reprocess_job(store: ObjectStore, session_factory: Callable[[], Session], job_id: uuid.UUID):
    """Re-process the same raw keys the original job listed in its manifest."""
    from app.pipeline.metrics import Metrics
    from app.pipeline.spark.config import SparkConfig
    from app.pipeline.spark.jobs.engine import SparkEngine
    from app.pipeline.spark.jobs.runner import ProcessingJobRunner
    from app.pipeline.spark.plan import ProcessingRequest, plan_inputs

    settings = get_settings()
    with session_factory() as db:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise LoadConfigError(f"processing job {job_id} not found")
        if job.status == ProcessingJobStatus.RUNNING.value:
            raise LoadConfigError(f"processing job {job_id} is still RUNNING; wait or confirm the process is gone")
        objects = _input_objects(store, job)
    plan = plan_inputs(
        store,
        ProcessingRequest(job.domain, objects=tuple(objects), output_prefix=None),
        max_window_days=settings.PROCESS_MAX_WINDOW_DAYS,
        max_objects=settings.PROCESS_MAX_INPUT_OBJECTS,
    )
    engine = SparkEngine(store, SparkConfig.from_settings(settings))
    try:
        return ProcessingJobRunner(
            store=store, session_factory=session_factory, engine=engine, metrics=Metrics(),
            code_version=settings.CODE_VERSION,
        ).run(plan)
    finally:
        engine.close()


def retry_ingestion(session_factory: Callable[[], Session], run_id: uuid.UUID) -> Dict[str, Any]:
    """Start a new ingest using the stored window. Refuses COMPLETED runs."""
    from app.core.settings import get_settings
    from app.pipeline.ingest import IngestionRunner
    from app.pipeline.kafka.producer import DeadLetterPublisher
    from app.pipeline.metrics import Metrics
    from app.pipeline.publisher import EventPublisher
    from app.pipeline.sources.base import IngestRequest
    from app.pipeline.sources.registry import Wiring, build_adapter

    settings = get_settings()
    with session_factory() as db:
        run = db.get(IngestionRun, run_id)
        if run is None:
            raise LoadConfigError(f"ingestion run {run_id} not found")
        if run.status == IngestionRunStatus.COMPLETED.value:
            raise LoadConfigError(
                f"ingestion run {run_id} already COMPLETED; start a new ingest instead of retrying"
            )
        if run.status == IngestionRunStatus.RUNNING.value:
            raise LoadConfigError(f"ingestion run {run_id} is still RUNNING")
        source = run.source
        params = dict(run.params or {})
        domain = source.domain
    try:
        start = datetime.fromisoformat(params["start"])
        end = datetime.fromisoformat(params["end"])
        request = IngestRequest(
            start=start if start.tzinfo else start.replace(tzinfo=timezone.utc),
            end=end if end.tzinfo else end.replace(tzinfo=timezone.utc),
            symbols=tuple(params.get("symbols") or ()),
            max_records=int(params.get("max_records") or settings.INGEST_MAX_RECORDS),
            page_size=int(params.get("page_size") or settings.INGEST_PAGE_SIZE),
            max_pages=int(params.get("max_pages") or settings.INGEST_MAX_PAGES),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LoadConfigError(f"run {run_id} params cannot rebuild an ingest request: {exc}") from exc
    metrics = Metrics()
    adapter = build_adapter(domain, settings, Wiring(metrics=metrics))
    from app.pipeline.kafka.config import producer_config
    from app.pipeline.kafka.producer import EventProducer
    from app.pipeline.retry import RetryPolicy

    producer = EventProducer(producer_config(settings), policy=RetryPolicy.from_settings(settings), metrics=metrics)
    dlq = DeadLetterPublisher(producer, settings.KAFKA_TOPIC_DLQ)
    publisher = EventPublisher(
        session_factory=session_factory, producer=producer, dlq=dlq,
        topics=settings.raw_topics, max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES,
    )
    runner = IngestionRunner(
        adapter=adapter, publisher=publisher, dlq=dlq, session_factory=session_factory,
        max_bytes=settings.KAFKA_MAX_MESSAGE_BYTES, metrics=metrics, trigger="retry",
    )
    try:
        return runner.run(request).as_dict()
    finally:
        adapter.close()
        producer.close()


def _require_manifest(store: ObjectStore, job: ProcessingJob) -> Dict[str, Any]:
    key = manifest_key(job.id)
    try:
        raw = store.get(key)
    except Exception as exc:
        raise LoadConfigError(f"manifest for job {job.id} is missing: {type(exc).__name__}") from exc
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise LoadConfigError(f"manifest for job {job.id} is not JSON") from exc
    if manifest.get("job_id") != str(job.id) or manifest.get("domain") != job.domain:
        raise LoadConfigError(f"manifest for job {job.id} does not match the job row")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise LoadConfigError(f"unsupported manifest version {manifest.get('manifest_version')}")
    schema = manifest.get("output", {}).get("schema_version") or manifest.get("processed_schema_version")
    if schema not in (None, PROCESSED_SCHEMA_VERSION):
        raise LoadConfigError(f"unsupported processed schema version {schema}")
    for file in manifest.get("output", {}).get("files", []):
        info = store.head(file.get("key", ""))
        if info is None:
            raise LoadConfigError(f"processed file {file.get('key')} is missing")
        if file.get("size") is not None and info.size != file["size"]:
            raise LoadConfigError(f"processed file {file.get('key')} size does not match the manifest")
    return manifest


def _input_objects(store: ObjectStore, job: ProcessingJob) -> list:
    manifest = _require_manifest(store, job)
    objects = list(manifest.get("input", {}).get("objects") or [])
    if not objects:
        raise LoadConfigError(f"job {job.id} manifest lists no input objects")
    missing = [key for key in objects if store.head(key) is None]
    if missing:
        raise LoadConfigError(f"job {job.id} raw objects missing: {missing[:5]}")
    return objects

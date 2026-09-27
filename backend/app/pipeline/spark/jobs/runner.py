"""One processing job: processing_jobs row → engine → rejects → manifest → lineage → finish.

The runner owns everything that is not Spark, so its lifecycle is testable with
a fake engine and no JVM:

- the ``processing_jobs`` row is committed RUNNING before any work and always
  finished: COMPLETED only after outputs, rejects and the manifest exist;
  FAILED (with the error) on any exception, which is then re-raised;
- reject objects are written write-once through the ObjectStore;
- ``ingestion_records`` of events this job added move landed → processed with
  ``processing_job_id`` (rows already processed by an earlier job keep theirs);
- an empty input finishes COMPLETED without starting Spark;
- one job at a time per output prefix (``jobs.lock``; Postgres advisory lock).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol

from sqlalchemy.orm import Session

from app.core.structured_logging import get_logger, log_event
from app.domain.enums import IngestionRecordStatus, ProcessingJobStatus, ProcessingMode
from app.domain.ingestion_models import ProcessingJob
from app.pipeline.lake import ObjectStore
from app.pipeline.metrics import Metrics
from app.pipeline.spark.jobs.lock import output_lock
from app.pipeline.spark.manifest import build_manifest, write_manifest
from app.pipeline.spark.plan import InputPlan
from app.pipeline.spark.writers import RejectRow, RejectSummary, output_files, write_rejects
from app.repositories import ingestion as repo
from app.repositories._upsert import chunks

_log = get_logger("quantpilot.pipeline.spark")


@dataclass
class ProcessingCounts:
    input_objects: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0  # same event_id more than once in the input
    already_processed_rows: int = 0  # event_id already in the target partition (existing row kept)
    existing_rows: int = 0  # rows read back from the target partitions before merging
    superseded_rows: int = 0  # older versions of a (source, external_id) removed
    output_rows: int = 0  # rows in the rewritten partitions
    new_rows: int = 0  # rows this job added

    @property
    def duplicates_removed(self) -> int:
        return self.duplicate_rows + self.already_processed_rows + self.superseded_rows

    def violations(self) -> List[str]:
        problems = []
        if self.input_objects != self.valid_rows + self.rejected_rows:
            problems.append(f"input_objects {self.input_objects} != valid {self.valid_rows} + rejected {self.rejected_rows}")
        merged = self.valid_rows - self.duplicate_rows - self.already_processed_rows + self.existing_rows
        if self.output_rows != merged - self.superseded_rows:
            problems.append(f"output_rows {self.output_rows} != merged {merged} - superseded {self.superseded_rows}")
        if self.new_rows > self.output_rows:
            problems.append(f"new_rows {self.new_rows} > output_rows {self.output_rows}")
        return problems

    def as_dict(self) -> Dict[str, int]:
        return {**asdict(self), "duplicates_removed": self.duplicates_removed}


@dataclass(frozen=True)
class JobInfo:
    job_id: uuid.UUID
    domain: str
    started_at: datetime
    output_prefix: str


@dataclass
class EngineResult:
    counts: ProcessingCounts
    output_partitions: List[str] = field(default_factory=list)
    rejects: Iterable[RejectRow] = ()
    rejects_by_code: Dict[str, int] = field(default_factory=dict)
    new_event_ids: Iterable[str] = ()
    app_id: Optional[str] = None
    release: Callable[[], None] = lambda: None


class Engine(Protocol):
    name: str
    version: Optional[str]

    def process(self, plan: InputPlan, job: JobInfo) -> EngineResult: ...

    @property
    def master(self) -> Optional[str]: ...


@dataclass
class JobResult:
    job_id: uuid.UUID
    domain: str
    status: ProcessingJobStatus
    counts: ProcessingCounts
    output_partitions: List[str]
    output_files: int
    rejects: RejectSummary
    manifest_uri: Optional[str]
    spark_app_id: Optional[str]
    duration_ms: int
    records_marked_processed: int = 0
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "domain": self.domain,
            "status": self.status.value,
            "counts": self.counts.as_dict(),
            "output_partitions": self.output_partitions,
            "output_files": self.output_files,
            "rejects": {"count": self.rejects.total, "written": self.rejects.written,
                        "already_recorded": self.rejects.already_recorded},
            "manifest_uri": self.manifest_uri,
            "spark_app_id": self.spark_app_id,
            "duration_ms": self.duration_ms,
            "records_marked_processed": self.records_marked_processed,
            "error": self.error,
        }


class ProcessingJobRunner:
    def __init__(
        self,
        *,
        store: ObjectStore,
        session_factory: Callable[[], Session],
        engine: Engine,
        metrics: Optional[Metrics] = None,
        code_version: Optional[str] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self._sessions = session_factory
        self.engine = engine
        self.metrics = metrics or Metrics()
        self.code_version = code_version or None
        self._now = now

    def run(self, plan: InputPlan) -> JobResult:
        with output_lock(self._sessions, plan.output_prefix):
            return self._tracked(plan)

    def _tracked(self, plan: InputPlan) -> JobResult:
        job_id, started, clock = uuid.uuid4(), self._now(), time.monotonic()
        job_name = f"spark.process.{plan.domain}"
        with self._sessions() as db:
            repo.start_processing_job(
                db, job_id=job_id, job_name=job_name, domain=plan.domain, mode=ProcessingMode.INCREMENTAL.value,
                since=plan.since, until=plan.until, input_prefix=plan.prefix, input_objects=len(plan.keys),
                code_version=self.code_version, engine_version=self._engine_version(),
            )
            db.commit()
        log_event(_log, logging.INFO, "pipeline.spark.job_started", job_id=str(job_id), domain=plan.domain,
                  input=plan.describe(), output_prefix=plan.output_prefix)
        state: Dict[str, Any] = {"counts": ProcessingCounts(input_objects=len(plan.keys)), "manifest_uri": None,
                                 "app_id": None, "partitions": []}
        try:
            return self._run(plan, job_id, job_name, started, clock, state)
        except BaseException as exc:
            self._fail(plan, job_id, job_name, started, clock, state, exc)
            raise

    def _run(self, plan, job_id, job_name, started, clock, state) -> JobResult:
        job = JobInfo(job_id, plan.domain, started, plan.output_prefix)
        result = self.engine.process(plan, job) if plan.keys else EngineResult(ProcessingCounts())
        state["app_id"], state["counts"] = result.app_id, result.counts
        try:
            rejects = write_rejects(self.store, result.rejects, job_id=job_id, processed_at=started)
            new_event_ids = list(result.new_event_ids)
        finally:
            result.release()
        counts = result.counts
        counts.input_objects = len(plan.keys)
        violations = counts.violations()
        if rejects.total != counts.rejected_rows:
            violations.append(f"wrote {rejects.total} reject objects for {counts.rejected_rows} rejected rows")
        if violations:
            raise RuntimeError("processing counter invariants violated: " + "; ".join(violations))
        partitions = sorted(result.output_partitions)
        state["partitions"] = partitions
        files = output_files(self.store, plan.output_prefix, partitions)
        finished = self._now()
        manifest = build_manifest(
            store=self.store, job_id=job_id, job_name=job_name, plan=plan, status=ProcessingJobStatus.COMPLETED.value,
            started_at=started, finished_at=finished, counts=counts.as_dict(), partitions=partitions, files=files,
            rejects={"count": rejects.total, "written": rejects.written, "already_recorded": rejects.already_recorded,
                     "by_code": result.rejects_by_code, "keys": rejects.keys},
            engine=self._engine_info(result.app_id), code_version=self.code_version,
        )
        state["manifest_uri"] = write_manifest(self.store, job_id, manifest)
        marked = 0
        with self._sessions() as db:
            for batch in chunks(new_event_ids, 500):
                marked += repo.advance_records(db, IngestionRecordStatus.PROCESSED, batch, processing_job_id=job_id)
            repo.finish_processing_job(
                db, db.get(ProcessingJob, job_id), status=ProcessingJobStatus.COMPLETED,
                metrics=self._job_metrics(counts, partitions), output_uri=self.store.uri(plan.output_prefix.rstrip("/")),
                manifest_uri=state["manifest_uri"], spark_app_id=result.app_id,
            )
            db.commit()
        duration_ms = int((time.monotonic() - clock) * 1000)
        self._observe(plan.domain, ProcessingJobStatus.COMPLETED, counts, duration_ms, result.rejects_by_code)
        log_event(_log, logging.INFO, "pipeline.spark.job_finished", job_id=str(job_id), domain=plan.domain,
                  status="COMPLETED", spark_app_id=result.app_id, duration_ms=duration_ms, counts=counts.as_dict(),
                  rejects_by_code=result.rejects_by_code, output_partitions=len(partitions), output_files=len(files),
                  records_marked_processed=marked, manifest_uri=state["manifest_uri"])
        return JobResult(job_id, plan.domain, ProcessingJobStatus.COMPLETED, counts, partitions, len(files), rejects,
                         state["manifest_uri"], result.app_id, duration_ms, marked)

    def _fail(self, plan, job_id, job_name, started, clock, state, exc: BaseException) -> None:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        counts: ProcessingCounts = state["counts"]
        if state["manifest_uri"] is None:
            try:
                state["manifest_uri"] = write_manifest(self.store, job_id, build_manifest(
                    store=self.store, job_id=job_id, job_name=job_name, plan=plan,
                    status=ProcessingJobStatus.FAILED.value, started_at=started, finished_at=self._now(),
                    counts=counts.as_dict(), partitions=state["partitions"], engine=self._engine_info(state["app_id"]),
                    code_version=self.code_version, error=error,
                ))
            except Exception as manifest_exc:  # the job error is what matters; say why there is no manifest
                log_event(_log, logging.WARNING, "pipeline.spark.failure_manifest_unwritten", job_id=str(job_id),
                          error=f"{type(manifest_exc).__name__}: {manifest_exc}"[:500])
        with self._sessions() as db:
            repo.finish_processing_job(
                db, db.get(ProcessingJob, job_id), status=ProcessingJobStatus.FAILED,
                metrics=self._job_metrics(counts, state["partitions"]), manifest_uri=state["manifest_uri"],
                error=error, spark_app_id=state["app_id"],
            )
            db.commit()
        duration_ms = int((time.monotonic() - clock) * 1000)
        self._observe(plan.domain, ProcessingJobStatus.FAILED, counts, duration_ms, {})
        log_event(_log, logging.ERROR, "pipeline.spark.job_failed", job_id=str(job_id), domain=plan.domain,
                  spark_app_id=state["app_id"], duration_ms=duration_ms, error=error)

    def _engine_version(self) -> Optional[str]:
        version = getattr(self.engine, "version", None)
        return f"{self.engine.name} {version}"[:32] if version else None

    def _engine_info(self, app_id: Optional[str]) -> Dict[str, Any]:
        return {"name": self.engine.name, "version": getattr(self.engine, "version", None),
                "master": getattr(self.engine, "master", None), "app_id": app_id}

    @staticmethod
    def _job_metrics(counts: ProcessingCounts, partitions: List[str]) -> Dict[str, Any]:
        return {"input_rows": counts.input_objects, "rejected_rows": counts.rejected_rows,
                "duplicates_removed": counts.duplicates_removed, "output_rows": counts.output_rows,
                "output_partitions": partitions}

    def _observe(self, domain: str, status: ProcessingJobStatus, counts: ProcessingCounts, duration_ms: int,
                 by_code: Dict[str, int]) -> None:
        self.metrics.inc("processing_jobs_total", domain=domain, status=status.value)
        self.metrics.observe("processing_job_duration_seconds", duration_ms / 1000, domain=domain)
        for kind in ("input_objects", "valid_rows", "rejected_rows", "duplicate_rows", "already_processed_rows",
                     "superseded_rows", "output_rows", "new_rows"):
            self.metrics.inc("processing_rows_total", getattr(counts, kind), domain=domain, kind=kind)
        for code, n in by_code.items():
            self.metrics.inc("processing_rejects_total", n, domain=domain, code=code)

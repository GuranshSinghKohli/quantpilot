"""One canonical load attempt of one processing job.

    lock (same advisory lock as processing, per processed/<domain>/ prefix)
    → load_status CANONICAL_LOAD_RUNNING (committed)
    → manifest → files (checked against the manifest)
    → pass 1: validate every row, write nothing
    → pass 2: per batch of ≤ batch_size rows of one file:
              BEGIN; canonical upsert; ingestion_records → loaded; COMMIT
    → retry rows that failed on a constraint once (companies: ticker moves)
    → reconcile → write-once load report → CANONICAL_LOAD_COMPLETE | _FAILED

Any exception marks the attempt FAILED (with the error) and is re-raised.
Committed batches stay: every write is newest-wins, so a retry of the whole
load reclassifies them as duplicates. Exactly-once is not claimed.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.structured_logging import get_logger, log_event
from app.domain.enums import CanonicalLoadStatus, IngestionRecordStatus, ProcessingJobStatus
from app.domain.ingestion_models import ProcessingJob
from app.pipeline.lake import ObjectStore
from app.pipeline.lake.keys import load_report_key, processed_prefix
from app.pipeline.load.domains import LOADERS, DomainLoader
from app.pipeline.load.errors import LoadConfigError, LoadValidationError, ReconciliationError, RowProblem
from app.pipeline.load.source import LoadSource, iter_rows, open_parquet, open_source, schema_problems
from app.pipeline.load.validation import check_record, lineage_problems, synthetic_allowed
from app.pipeline.spark.jobs.lock import output_lock
from app.repositories import ingestion as repo
from app.repositories._upsert import chunks
from app.repositories.canonical import UpsertStats

_log = get_logger("quantpilot.pipeline.load")

DEFAULT_BATCH_SIZE = 1000
_MAX_PROBLEMS = 1000  # kept in the report; all are counted


@dataclass
class LoadCounts:
    candidates: int = 0  # rows read from the manifest's files (pass 2)
    validated: int = 0  # rows checked in pass 1
    inserted: int = 0
    updated: int = 0
    stale: int = 0
    duplicates: int = 0
    failed: int = 0
    records_loaded: int = 0  # ingestion_records moved to loaded
    batches: int = 0

    def add(self, stats: UpsertStats) -> None:
        self.inserted += stats.inserted
        self.updated += stats.updated
        self.stale += stats.stale
        self.duplicates += stats.duplicate
        self.failed += len(stats.failed)

    def violations(self, expected_rows: Optional[int]) -> List[str]:
        problems = []
        outcomes = self.inserted + self.updated + self.stale + self.duplicates + self.failed
        if self.candidates != outcomes:
            problems.append(
                f"candidates {self.candidates} != inserted {self.inserted} + updated {self.updated} + stale "
                f"{self.stale} + duplicates {self.duplicates} + failed {self.failed}"
            )
        if expected_rows is not None and self.candidates != expected_rows:
            problems.append(f"candidates {self.candidates} != manifest output_rows {expected_rows}")
        if self.validated != self.candidates:
            problems.append(f"validated {self.validated} != loaded candidates {self.candidates}")
        if self.records_loaded > self.inserted + self.updated + self.duplicates:
            problems.append(f"records_loaded {self.records_loaded} > current versions "
                            f"{self.inserted + self.updated + self.duplicates}")
        return problems

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


@dataclass
class LoadResult:
    job_id: uuid.UUID
    attempt_id: uuid.UUID
    domain: str
    status: CanonicalLoadStatus
    counts: LoadCounts
    partitions: List[str]
    files: int
    report_uri: Optional[str]
    duration_ms: int
    timings_ms: Dict[str, int] = field(default_factory=dict)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "attempt_id": str(self.attempt_id),
            "domain": self.domain,
            "status": self.status.value,
            "counts": self.counts.as_dict(),
            "partitions": self.partitions,
            "files": self.files,
            "report_uri": self.report_uri,
            "duration_ms": self.duration_ms,
            "timings_ms": self.timings_ms,
            "failures": self.failures[:50],
            "error": self.error,
        }


class CanonicalLoadRunner:
    def __init__(
        self,
        *,
        store: ObjectStore,
        session_factory: Callable[[], Session],
        batch_size: int = DEFAULT_BATCH_SIZE,
        allow_synthetic: bool = False,
        after_commit: Optional[Callable[[int], None]] = None,
    ) -> None:
        if batch_size < 1:
            raise LoadConfigError("batch size must be at least 1")
        self.store = store
        self._sessions = session_factory
        self.batch_size = batch_size
        self.allow_synthetic = allow_synthetic
        self._after_commit = after_commit  # test hook: called with the batch number after each commit

    def run(self, job_id: uuid.UUID, *, since: Optional[date] = None, until: Optional[date] = None) -> LoadResult:
        with self._sessions() as db:
            job = db.get(ProcessingJob, job_id)
            if job is None:
                raise LoadConfigError(f"processing job {job_id} not found")
            domain = job.domain
            if job.status != ProcessingJobStatus.COMPLETED.value:
                raise LoadConfigError(f"processing job {job_id} is {job.status}; only COMPLETED jobs can be loaded")
            if self.allow_synthetic and not synthetic_allowed(db):
                raise LoadConfigError("--allow-synthetic is only accepted on a test database (name contains 'test')")
        loader = LOADERS[domain]
        with output_lock(self._sessions, processed_prefix(domain)):
            return self._attempt(job_id, loader, since, until)

    def _attempt(self, job_id: uuid.UUID, loader: DomainLoader, since, until) -> LoadResult:
        attempt_id, clock = uuid.uuid4(), time.monotonic()
        with self._sessions() as db:
            job = db.get(ProcessingJob, job_id)
            previous = job.load_status
            if previous == CanonicalLoadStatus.RUNNING.value:
                # We hold the lock, so that attempt's process is gone (crash or kill).
                log_event(_log, logging.WARNING, "pipeline.load.unfinished_attempt_replaced", job_id=str(job_id),
                          attempt=job.load_attempts, started_at=job.load_started_at.isoformat() if job.load_started_at else None)
            repo.start_load(db, job)
            db.commit()
        log_event(_log, logging.INFO, "pipeline.load.started", job_id=str(job_id), attempt_id=str(attempt_id),
                  domain=loader.domain, previous_status=previous)
        state: Dict[str, Any] = {"counts": LoadCounts(), "partitions": [], "files": 0, "timings": {}, "failures": []}
        try:
            return self._load(job_id, attempt_id, loader, since, until, clock, state)
        except BaseException as exc:
            self._fail(job_id, attempt_id, loader, clock, state, exc)
            raise

    # --- passes ------------------------------------------------------------------

    def _load(self, job_id, attempt_id, loader: DomainLoader, since, until, clock, state) -> LoadResult:
        counts: LoadCounts = state["counts"]
        with self._sessions() as db:
            source = open_source(self.store, db.get(ProcessingJob, job_id), since=since, until=until)
        state["partitions"], state["files"] = source.partitions, len(source.files)

        tick = time.monotonic()
        counts.validated = self._validate(source, loader)
        state["timings"]["validate"] = int((time.monotonic() - tick) * 1000)

        tick = time.monotonic()
        retry: List[Any] = []
        for file in source.files:
            parquet = open_parquet(self.store, file)
            for records in iter_rows(parquet, self.batch_size):
                rows = [loader.to_row(rec) for rec in records]
                counts.candidates += len(rows)
                with self._sessions() as db:
                    try:
                        stats = loader.upsert(db, rows)
                        counts.records_loaded += self._mark_loaded(db, stats.current)
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
                counts.add(stats)
                counts.batches += 1
                retry.extend(stats.failed_rows)
                state["failures"].extend({**f, "file": file.key, "pass": "load"} for f in stats.failed)
                if self._after_commit is not None:
                    self._after_commit(counts.batches)
        state["timings"]["load"] = int((time.monotonic() - tick) * 1000)

        if retry:
            # A constraint conflict can be an ordering artifact (a ticker moving
            # between two CIKs in the same snapshot): retry those rows once.
            with self._sessions() as db:
                try:
                    again = loader.upsert(db, retry)
                    counts.records_loaded += self._mark_loaded(db, again.current)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            counts.failed -= len(retry)
            counts.add(again)
            state["failures"].extend({**f, "pass": "retry"} for f in again.failed)

        violations = counts.violations(source.expected_rows)
        if violations:
            raise ReconciliationError("load reconciliation failed: " + "; ".join(violations))

        status = CanonicalLoadStatus.COMPLETE if counts.failed == 0 else CanonicalLoadStatus.FAILED
        error = None
        if counts.failed:
            final = [f for f in state["failures"] if f["pass"] == "retry"]
            shown = "; ".join(f"cik {f.get('cik')} event {f['event_id']}: {f['error']}" for f in final[:20])
            error = f"{counts.failed} row(s) failed canonical constraints (other rows loaded): {shown}"
        return self._finish(job_id, attempt_id, loader, source, status, clock, state, error)

    def _validate(self, source: LoadSource, loader: DomainLoader) -> int:
        problems: List[RowProblem] = []
        total = 0
        for file in source.files:
            parquet = open_parquet(self.store, file)
            schema = schema_problems(loader.domain, parquet.schema_arrow)
            if schema:
                problems.append(RowProblem(file.key, -1, None, "schema: " + "; ".join(schema)))
                continue
            offset = 0
            for records in iter_rows(parquet, self.batch_size):
                typed = []
                for i, rec in enumerate(records):
                    row, found = check_record(loader, rec, allow_synthetic=self.allow_synthetic, raw_uri=self.store.uri)
                    problems.extend(RowProblem(file.key, offset + i, rec.get("event_id"), p) for p in found)
                    if row is not None and not found:
                        typed.append((offset + i, row))
                with self._sessions() as db:
                    by_event = lineage_problems(db, loader.domain, [r for _, r in typed])
                for index, row in typed:
                    problems.extend(RowProblem(file.key, index, row.event_id, p) for p in by_event.get(row.event_id, []))
                offset += len(records)
            total += offset
        if problems:
            raise LoadValidationError(problems)
        return total

    def _mark_loaded(self, db: Session, event_ids: List[str]) -> int:
        moved = 0
        for batch in chunks(event_ids, 500):
            moved += repo.advance_records(db, IngestionRecordStatus.LOADED, batch)
        return moved

    # --- finish ------------------------------------------------------------------

    def _report(self, job_id, attempt_id, loader, status, state, error, manifest_uri) -> Optional[str]:
        counts: LoadCounts = state["counts"]
        document = {
            "report_version": 1,
            "kind": "canonical_load",
            "job_id": str(job_id),
            "attempt_id": str(attempt_id),
            "domain": loader.domain,
            "target": loader.table,
            "status": status.value,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "manifest_uri": manifest_uri,
            "partitions": state["partitions"],
            "files": state["files"],
            "batch_size": self.batch_size,
            "counts": counts.as_dict(),
            "timings_ms": state["timings"],
            "failures": state["failures"][:_MAX_PROBLEMS],
            "problems": state.get("problems", [])[:_MAX_PROBLEMS],
            "error": error,
        }
        key = load_report_key(job_id, attempt_id)
        put = self.store.put_if_absent(key, json.dumps(document, indent=2, sort_keys=True, default=str).encode(),
                                       content_type="application/json")
        return put.uri

    def _finish(self, job_id, attempt_id, loader, source: LoadSource, status, clock, state, error) -> LoadResult:
        counts: LoadCounts = state["counts"]
        report_uri = self._report(job_id, attempt_id, loader, status, state, error, source.manifest_uri)
        with self._sessions() as db:
            repo.finish_load(db, db.get(ProcessingJob, job_id), status=status, counts=self._job_counts(counts),
                             partitions=state["partitions"], error=error, report_uri=report_uri)
            db.commit()
        duration_ms = int((time.monotonic() - clock) * 1000)
        log_event(_log, logging.INFO if status is CanonicalLoadStatus.COMPLETE else logging.ERROR,
                  "pipeline.load.finished", job_id=str(job_id), attempt_id=str(attempt_id), domain=loader.domain,
                  status=status.value, counts=counts.as_dict(), partitions=len(state["partitions"]),
                  files=state["files"], duration_ms=duration_ms, timings_ms=state["timings"], report_uri=report_uri)
        return LoadResult(job_id, attempt_id, loader.domain, status, counts, state["partitions"], state["files"],
                          report_uri, duration_ms, state["timings"], state["failures"], error)

    def _fail(self, job_id, attempt_id, loader, clock, state, exc: BaseException) -> None:
        error = f"{type(exc).__name__}: {exc}"[:8000]
        if isinstance(exc, LoadValidationError):
            state["problems"] = [p.as_dict() for p in exc.problems]
        report_uri = None
        try:
            report_uri = self._report(job_id, attempt_id, loader, CanonicalLoadStatus.FAILED, state, error, None)
        except Exception as report_exc:  # the load error is what matters; say why there is no report
            log_event(_log, logging.WARNING, "pipeline.load.failure_report_unwritten", job_id=str(job_id),
                      error=f"{type(report_exc).__name__}: {report_exc}"[:500])
        with self._sessions() as db:
            repo.finish_load(db, db.get(ProcessingJob, job_id), status=CanonicalLoadStatus.FAILED,
                             counts=self._job_counts(state["counts"]), partitions=state["partitions"],
                             error=error, report_uri=report_uri)
            db.commit()
        log_event(_log, logging.ERROR, "pipeline.load.failed", job_id=str(job_id), attempt_id=str(attempt_id),
                  domain=loader.domain, error=error[:2000], counts=state["counts"].as_dict(),
                  duration_ms=int((time.monotonic() - clock) * 1000), report_uri=report_uri)

    @staticmethod
    def _job_counts(counts: LoadCounts) -> Dict[str, int]:
        return {"candidates": counts.candidates, "inserted": counts.inserted, "updated": counts.updated,
                "stale": counts.stale, "duplicates": counts.duplicates, "failed": counts.failed}

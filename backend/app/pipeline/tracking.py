"""Ingestion run tracking.

A run answers: which source ran, when, how long, how many records were
received / valid / invalid / duplicate / published / failed to publish, and
the final status. Downstream progress (landed, dlq, processed, loaded) is read
from ``ingestion_records`` by ``run_summary`` instead of being copied onto the run.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.structured_logging import get_logger, log_event
from app.domain.enums import IngestionRecordStatus, IngestionRunStatus
from app.domain.ingestion_models import IngestionRun
from app.pipeline.contracts import SourceSpec
from app.repositories import ingestion as repo
from app.repositories.ingestion import RunCounters

_log = get_logger("quantpilot.pipeline.tracking")


def counter_violations(c: RunCounters) -> List[str]:
    """Bookkeeping invariants a finished run must satisfy."""
    problems = []
    if c.received != c.valid + c.invalid:
        problems.append(f"received {c.received} != valid {c.valid} + invalid {c.invalid}")
    if c.valid != c.published + c.duplicate + c.publish_failed:
        problems.append(
            f"valid {c.valid} != published {c.published} + duplicate {c.duplicate}"
            f" + publish_failed {c.publish_failed}"
        )
    return problems


def derive_run_status(c: RunCounters, fatal_error: Optional[str] = None) -> IngestionRunStatus:
    """COMPLETED: everything received is safely in the system (published or a known
    duplicate), including the empty run. PARTIAL: some records made it and some
    did not, or the run stopped early. FAILED: nothing made it and something went wrong."""
    delivered = c.published + c.duplicate
    went_wrong = bool(fatal_error) or c.invalid > 0 or c.publish_failed > 0
    if not went_wrong:
        return IngestionRunStatus.COMPLETED
    return IngestionRunStatus.PARTIAL if delivered > 0 else IngestionRunStatus.FAILED


class RunTracker:
    """Owns the ``ingestion_runs`` row for one adapter execution."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        spec: SourceSpec,
        *,
        trigger: str = "cli",
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._sessions = session_factory
        self.spec = spec
        self.trigger = trigger
        self.params = params or {}
        self.counters = RunCounters()
        self.run_id: Optional[uuid.UUID] = None
        self.source_id: Optional[uuid.UUID] = None

    def start(self) -> uuid.UUID:
        with self._sessions() as db:
            source = repo.ensure_source(
                db,
                key=self.spec.key,
                domain=self.spec.domain.value,
                provider=self.spec.provider,
                description=self.spec.description,
            )
            run = repo.start_run(db, source_id=source.id, trigger=self.trigger, params=self.params)
            db.commit()
            self.run_id, self.source_id = run.id, source.id
        log_event(_log, logging.INFO, "pipeline.ingest.run_started", run_id=str(self.run_id), source=self.spec.key, params=self.params)
        return self.run_id

    def finish(self, fatal_error: Optional[str] = None) -> IngestionRunStatus:
        if self.run_id is None:
            raise RuntimeError("finish() before start()")
        violations = counter_violations(self.counters)
        error = fatal_error
        if violations:
            error = "; ".join(filter(None, [fatal_error, "counter invariants violated: " + "; ".join(violations)]))
        status = derive_run_status(self.counters, error)
        with self._sessions() as db:
            run = db.get(IngestionRun, self.run_id)
            repo.finish_run(db, run, status=status, counters=self.counters, error=error)
            db.commit()
            duration_ms = run.duration_ms
        level = logging.INFO if status == IngestionRunStatus.COMPLETED else logging.WARNING
        log_event(
            _log,
            level,
            "pipeline.ingest.run_finished",
            run_id=str(self.run_id),
            source=self.spec.key,
            status=status.value,
            duration_ms=duration_ms,
            records_received=self.counters.received,
            records_valid=self.counters.valid,
            records_invalid=self.counters.invalid,
            records_duplicate=self.counters.duplicate,
            records_published=self.counters.published,
            records_publish_failed=self.counters.publish_failed,
            error=error,
        )
        return status


def run_summary(db: Session, run_id: uuid.UUID) -> Dict[str, Any]:
    run = db.get(IngestionRun, run_id)
    if run is None:
        raise LookupError(f"no ingestion run {run_id}")
    progress = repo.run_progress(db, run_id)
    S = IngestionRecordStatus
    return {
        "run_id": str(run.id),
        "source": run.source.key,
        "status": run.status,
        "trigger": run.trigger,
        "params": run.params,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": run.duration_ms,
        "received": run.records_received,
        "valid": run.records_valid,
        "invalid": run.records_invalid,
        "duplicates": run.records_duplicate,
        "published": run.records_published,
        "publish_failed": run.records_publish_failed,
        "landed": sum(progress.get(s.value, 0) for s in (S.LANDED, S.PROCESSED, S.LOADED)),
        "dlq": progress.get(S.DLQ.value, 0),
        "processed": sum(progress.get(s.value, 0) for s in (S.PROCESSED, S.LOADED)),
        "loaded": progress.get(S.LOADED.value, 0),
        "error": run.error,
    }

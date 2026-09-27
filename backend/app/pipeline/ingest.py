"""Ingestion orchestration: one adapter execution = one tracked run.

    RunTracker.start()                                   ingestion_runs RUNNING (committed)
    for stream in adapter.streams(request):              bounded by max_records / max_pages
        for page in stream.pages():
            normalize each item → record → build_event(original=item.original)
              invalid → counted + DLQ (component "ingestor")
            in-run dedup by event_id → EventPublisher.publish (Step 2.4)
    RunTracker.finish(errors)                            COMPLETED | PARTIAL | FAILED

A failed stream is recorded and the next stream runs, unless the failure is
run-fatal (auth, an over-budget rate-limit window). Any other exception
finishes the run as FAILED before it propagates.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from pydantic import ValidationError

from app.core.pipeline_context import pipeline_bind
from app.core.structured_logging import get_logger, log_event
from app.domain.enums import IngestionRunStatus
from app.domain.ingestion_models import IngestionRun
from app.pipeline.contracts import build_event
from app.pipeline.dlq import dead_letter, deterministic_reject_id
from app.pipeline.errors import PermanentError, PipelineError, RetryExhausted
from app.pipeline.kafka.producer import DeadLetterPublisher
from app.pipeline.metrics import Metrics
from app.pipeline.publisher import EventPublisher
from app.pipeline.sources.base import IngestRequest, NormalizationError, SourceAdapter, SourceItem, validation_details
from app.pipeline.sources.http import SourceRequestError
from app.pipeline.tracking import RunTracker

_log = get_logger("quantpilot.pipeline.ingest")


@dataclass
class StreamResult:
    label: str
    pages: int = 0
    fetched: int = 0
    truncated: bool = False
    error_kind: Optional[str] = None
    error: Optional[str] = None


@dataclass
class IngestResult:
    run_id: str
    source: str
    provider: str
    status: IngestionRunStatus
    received: int
    valid: int
    invalid: int
    published: int
    duplicate: int
    publish_failed: int
    dead_lettered_invalid: int
    record_limit_reached: bool
    streams: List[StreamResult] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> Dict:
        out = dict(self.__dict__)
        out["status"] = self.status.value
        out["streams"] = [s.__dict__ for s in self.streams]
        return out


def _describe(exc: BaseException) -> tuple[str, str, bool]:
    """(kind, message, fatal_for_run) for a stream failure."""
    root = exc.last_error if isinstance(exc, RetryExhausted) else exc
    if isinstance(root, SourceRequestError):
        kind = root.kind.value + ("_retries_exhausted" if isinstance(exc, RetryExhausted) else "")
        return kind, str(exc)[:500], root.fatal_for_run
    return type(root).__name__, str(exc)[:500], False


class IngestionRunner:
    def __init__(
        self,
        *,
        adapter: SourceAdapter,
        publisher: EventPublisher,
        dlq: DeadLetterPublisher,
        session_factory,
        max_bytes: int,
        metrics: Optional[Metrics] = None,
        trigger: str = "cli",
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.adapter = adapter
        self.publisher = publisher
        self.dlq = dlq
        self._sessions = session_factory
        self.max_bytes = max_bytes
        self.metrics = metrics or Metrics()
        self.trigger = trigger
        self._now = now

    def run(self, request: IngestRequest) -> IngestResult:
        source = self.adapter.source
        tracker = RunTracker(self._sessions, self.adapter.spec, trigger=self.trigger,
                             params={"provider": self.adapter.provider, **request.params()})
        tracker.start()
        streams: List[StreamResult] = []
        errors: List[str] = []
        seen: Set[str] = set()
        self._rejected_to_dlq = 0
        remaining = request.max_records
        with pipeline_bind(component="ingestor", operation="ingest", domain=self.adapter.spec.domain.value,
                           source=source, run_id=str(tracker.run_id), correlation_id=str(tracker.run_id)):
            return self._run(request, tracker, source, streams, errors, seen, remaining)

    def _run(self, request, tracker, source, streams, errors, seen, remaining) -> IngestResult:
        try:
            for stream in self.adapter.streams(request):
                if remaining <= 0:
                    streams.append(StreamResult(stream.label, truncated=True))
                    continue
                result = StreamResult(stream.label)
                streams.append(result)
                try:
                    for page in stream.pages():
                        result.pages += 1
                        items = page.items[:remaining]
                        self._handle(tracker, items, seen, errors)
                        remaining -= len(items)
                        result.fetched += len(items)
                        result.truncated = page.has_more or len(items) < len(page.items)
                        log_event(_log, logging.INFO, "pipeline.source.page", source=source,
                                  provider=self.adapter.provider, run_id=str(tracker.run_id), stream=stream.label,
                                  page=result.pages, items=len(page.items), accepted=len(items),
                                  has_more=page.has_more)
                        if remaining <= 0:
                            break
                except (SourceRequestError, RetryExhausted) as exc:
                    result.error_kind, result.error, fatal = _describe(exc)
                    errors.append(f"{stream.label}: {result.error_kind}: {result.error}")
                    if fatal:
                        log_event(_log, logging.ERROR, "pipeline.source.run_aborted", source=source,
                                  run_id=str(tracker.run_id), stream=stream.label, kind=result.error_kind)
                        break
                finally:
                    log_event(_log, logging.WARNING if result.error else logging.INFO,
                              "pipeline.source.stream_finished", source=source, provider=self.adapter.provider,
                              run_id=str(tracker.run_id), stream=result.label, pages=result.pages,
                              fetched=result.fetched, truncated=result.truncated, error_kind=result.error_kind)
        except BaseException as exc:
            tracker.finish(fatal_error=f"{type(exc).__name__}: {exc}"[:1000])
            raise

        status = tracker.finish(fatal_error="; ".join(errors)[:4000] or None)
        c = tracker.counters
        self._observe(source, status, c, tracker)
        outcome = IngestResult(
            run_id=str(tracker.run_id), source=source, provider=self.adapter.provider, status=status,
            received=c.received, valid=c.valid, invalid=c.invalid, published=c.published,
            duplicate=c.duplicate, publish_failed=c.publish_failed,
            dead_lettered_invalid=self._rejected_to_dlq, record_limit_reached=remaining <= 0,
            streams=streams, error="; ".join(errors) or None,
        )
        log_event(_log, logging.INFO, "pipeline.source.run_summary", **{k: v for k, v in outcome.as_dict().items()
                                                                        if k != "streams"})
        return outcome

    def _handle(self, tracker: RunTracker, items: List[SourceItem], seen: Set[str], errors: List[str]) -> None:
        if not items:
            return
        source = self.adapter.source
        now = self._now()
        c = tracker.counters
        c.received += len(items)
        self.metrics.inc("ingestion_records_total", len(items), source=source)
        events = []
        for item in items:
            try:
                record = self.adapter.normalize(item, ingested_at=now)
                try:
                    event = build_event(record, run_id=tracker.run_id, original=item.original, ingested_at=now)
                except ValidationError as exc:  # e.g. a raw payload that is not plain JSON
                    raise NormalizationError(f"item {item.ref} cannot form an event",
                                             validation_details(exc)) from exc
            except PermanentError as exc:
                c.invalid += 1
                self.metrics.inc("ingestion_records_failed_total", source=source)
                self._reject(tracker, item, exc, now, errors)
                continue
            if event.event_id in seen:
                c.valid += 1
                c.duplicate += 1
                self.metrics.inc("ingestion_records_duplicate_total", source=source)
                continue
            seen.add(event.event_id)
            events.append(event)
        if events:
            self.publisher.publish(tracker, events)

    def _reject(self, tracker: RunTracker, item: SourceItem, error: PermanentError, now: datetime,
                errors: List[str]) -> None:
        source = self.adapter.source
        raw = json.dumps(item.original, sort_keys=True, default=str).encode("utf-8")
        log_event(_log, logging.WARNING, "pipeline.source.record_rejected", source=source,
                  run_id=str(tracker.run_id), ref=item.ref, reason=error.reason.value,
                  fields=[d.get("field") for d in error.details][:20], error=str(error)[:300])
        letter = dead_letter(
            component="ingestor", error=error, original_value=raw, max_bytes=self.max_bytes, first_failed_at=now,
            source=source, run_id=tracker.run_id, original_topic=self.publisher.topics[self.adapter.spec.domain.value],
            original_key=item.ref.encode("utf-8"),
            dlq_id=deterministic_reject_id(source, hashlib.sha256(raw).hexdigest(), error.reason.value),
        )
        try:
            self.dlq.publish(letter)
            self._rejected_to_dlq += 1
            self.metrics.inc("kafka_dlq_messages_total", component="ingestor", reason=error.reason.value)
        except PipelineError as exc:
            # Still counted invalid; the run records that the reject was not preserved.
            log_event(_log, logging.ERROR, "pipeline.dlq.publish_failed", component="ingestor", source=source,
                      ref=item.ref, error=str(exc)[:300])
            errors.append(f"reject {item.ref} could not be dead-lettered: {str(exc)[:200]}")

    def _observe(self, source: str, status: IngestionRunStatus, c, tracker: RunTracker) -> None:
        self.metrics.inc("ingestion_runs_total", source=source, status=status.value)
        if c.published:
            self.metrics.inc("ingestion_records_published_total", c.published, source=source)
        if c.publish_failed:
            self.metrics.inc("ingestion_records_failed_total", c.publish_failed, source=source)
        if tracker.run_id is None:
            return
        with self._sessions() as db:
            run = db.get(IngestionRun, tracker.run_id)
            if run is not None and run.duration_ms:
                self.metrics.observe("ingestion_duration_seconds", run.duration_ms / 1000, source=source)

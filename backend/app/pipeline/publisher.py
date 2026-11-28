"""Tracked publishing: ingestion_records first, Kafka second, status last.

1. ``claim_event`` per event (committed), so a crash leaves ``pending`` rows that
   the next run re-publishes (RETRY) and delivered events are skipped (DUPLICATE).
2. Publish and wait for delivery reports.
3. Acknowledged → ``published`` with the Kafka partition/offset; not acknowledged
   → ``publish_failed`` with the reason. Nothing advances on "produce() returned".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Sequence

from sqlalchemy.orm import Session

from app.core.structured_logging import get_logger, log_event
from app.domain.enums import IngestionRecordStatus
from app.pipeline.dlq import dead_letter
from app.pipeline.errors import PermanentError, PipelineError
from app.pipeline.kafka.producer import (
    DeadLetterPublisher,
    Delivery,
    EventProducer,
    OutboundMessage,
    PublishFailure,
)
from app.pipeline.serde import Event, encode_event, event_headers
from app.pipeline.tracking import RunTracker
from app.repositories import ingestion as repo

_log = get_logger("quantpilot.pipeline.publisher")


@dataclass
class PublishSummary:
    published: int = 0
    duplicate: int = 0
    publish_failed: int = 0
    dead_lettered: int = 0
    deliveries: Dict[str, Delivery] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)


class EventPublisher:
    COMPONENT = "ingestor"

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        producer: EventProducer,
        dlq: DeadLetterPublisher,
        topics: Dict[str, str],
        max_bytes: int,
    ) -> None:
        self._sessions = session_factory
        self.producer = producer
        self.dlq = dlq
        self.topics = topics
        self.max_bytes = max_bytes

    def publish(self, tracker: RunTracker, events: Sequence[Event]) -> PublishSummary:
        if tracker.run_id is None:
            raise RuntimeError("start the run before publishing")
        summary = PublishSummary()
        unique: Dict[str, Event] = {}
        for event in events:
            if event.run_id != tracker.run_id:
                raise ValueError(f"event {event.event_id} belongs to run {event.run_id}, not {tracker.run_id}")
            if event.event_id in unique:
                summary.duplicate += 1
            else:
                unique[event.event_id] = event

        to_send: List[Event] = []
        with self._sessions() as db:
            for event in unique.values():
                ref = repo.EventRef(event.event_id, event.domain, event.external_id, event.dedup_key,
                                    event.content_hash, event.schema_version)
                claim = repo.claim_event(db, ref, run_id=tracker.run_id, source_id=tracker.source_id,
                                         topic=self.topics[event.domain])
                if claim is repo.Claim.DUPLICATE:
                    summary.duplicate += 1
                else:
                    to_send.append(event)
            db.commit()

        messages, sent = [], []
        for event in to_send:
            try:
                value = encode_event(event, self.max_bytes)
            except PermanentError as exc:
                self._dead_letter(event, exc, summary)
                continue
            messages.append(OutboundMessage(self.topics[event.domain], event.event_id, value, event_headers(event)))
            sent.append(event)
        results = self.producer.publish_many(messages)

        with self._sessions() as db:
            delivered = [(r.key, r.partition, r.offset) for r in results if isinstance(r, Delivery)]
            repo.record_deliveries(db, delivered)
            for event, result in zip(sent, results):
                if isinstance(result, PublishFailure):
                    summary.publish_failed += 1
                    summary.failures[event.event_id] = str(result.error)[:500]
                    repo.advance_records(db, IngestionRecordStatus.PUBLISH_FAILED, [event.event_id],
                                         reason=str(result.error))
                else:
                    summary.published += 1
                    summary.deliveries[event.event_id] = result
            db.commit()

        c = tracker.counters
        c.valid += len(events)
        c.published += summary.published
        c.duplicate += summary.duplicate
        c.publish_failed += summary.publish_failed + summary.dead_lettered
        log_event(_log, logging.INFO, "pipeline.publish.batch", run_id=str(tracker.run_id),
                  events=len(events), published=summary.published, duplicate=summary.duplicate,
                  publish_failed=summary.publish_failed, dead_lettered=summary.dead_lettered)
        return summary

    def _dead_letter(self, event: Event, error: PermanentError, summary: PublishSummary) -> None:
        now = datetime.now(timezone.utc)
        letter = dead_letter(
            component=self.COMPONENT, error=error, original_value=event.model_dump_json().encode("utf-8"),
            max_bytes=self.max_bytes, first_failed_at=now, source=event.source, event_id=event.event_id,
            run_id=event.run_id, original_topic=self.topics[event.domain], original_key=event.event_id.encode(),
        )
        try:
            self.dlq.publish(letter)
        except PipelineError as exc:
            # Leave the event retryable rather than pretend it was dead-lettered.
            log_event(_log, logging.ERROR, "pipeline.dlq.publish_failed", component=self.COMPONENT,
                      event_id=event.event_id, reason=error.reason.value, error=str(exc)[:300])
            with self._sessions() as db:
                repo.advance_records(db, IngestionRecordStatus.PUBLISH_FAILED, [event.event_id],
                                     reason=f"{error.reason.value}; DLQ publish failed: {exc}")
                db.commit()
            summary.publish_failed += 1
            summary.failures[event.event_id] = str(exc)[:500]
            return
        with self._sessions() as db:
            repo.advance_records(db, IngestionRecordStatus.DLQ, [event.event_id], reason=f"{error.reason.value}: {error}")
            db.commit()
        summary.dead_lettered += 1
        summary.failures[event.event_id] = f"{error.reason.value}: {error}"
        log_event(_log, logging.WARNING, "pipeline.dlq.published", component=self.COMPONENT,
                  dlq_id=str(letter.dlq_id), event_id=event.event_id, reason=error.reason.value)

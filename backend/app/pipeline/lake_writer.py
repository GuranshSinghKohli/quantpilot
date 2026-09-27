"""Lake writer: raw Kafka events → write-once raw objects → ingestion_records.

Per message: decode → validate → routing checks → lineage check → put_if_absent
→ record_landing (DB commit) → Kafka offset commit. The offset is committed
only after the object and the record are durable, so a crash anywhere before
it means redelivery, which the write-once store and forward-only statuses make
harmless. Permanent failures and exhausted retries go to the DLQ (acknowledged)
before the offset is committed; if the DLQ publish fails nothing is committed.
Unexpected exceptions propagate so the process exits without committing.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.core.structured_logging import get_logger, log_event
from app.domain.enums import IngestionRecordStatus
from app.pipeline.dlq import dead_letter, deterministic_dlq_id, failure_reason
from app.pipeline.errors import FailureReason, PermanentError, RetryExhausted
from app.pipeline.kafka.consumer import committed_lag
from app.pipeline.kafka.producer import DeadLetterPublisher
from app.pipeline.lake import IntegrityError as StoredBytesCorrupt
from app.pipeline.lake import InvalidKey, ObjectStore, PutResult
from app.pipeline.lake.keys import raw_key_for
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy, call_with_retry, is_transient
from app.pipeline.serde import Event, decode_event, encode_event
from app.pipeline.validation import check_event
from app.repositories import ingestion as repo

_log = get_logger("quantpilot.pipeline.lake_writer")
_EVENT_ID = re.compile(r"[0-9a-f]{64}")


class Outcome(StrEnum):
    LANDED = "landed"  # first write of this object
    REPLAY = "replay"  # object already stored with identical bytes
    DEAD_LETTERED = "dead_lettered"


@dataclass(frozen=True)
class HandleResult:
    outcome: Outcome
    topic: str
    partition: int
    offset: int
    event_id: Optional[str] = None
    raw_uri: Optional[str] = None
    tracked: Optional[bool] = None  # False: no ingestion_records row for the event
    dlq_id: Optional[uuid.UUID] = None
    failure_reason: Optional[str] = None


class LakeWriter:
    COMPONENT = "lake_writer"

    def __init__(
        self,
        *,
        consumer,
        store: ObjectStore,
        session_factory: Callable[[], Session],
        dlq: DeadLetterPublisher,
        topics: Dict[str, str],
        max_bytes: int,
        policy: RetryPolicy,
        metrics: Optional[Metrics] = None,
        poll_timeout: float = 1.0,
        lag_interval_s: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        before_commit: Optional[Callable[[object, HandleResult], None]] = None,
    ) -> None:
        self.consumer = consumer
        self.store = store
        self._sessions = session_factory
        self.dlq = dlq
        self.topics = dict(topics)
        self.max_bytes = max_bytes
        self.policy = policy
        self.metrics = metrics or Metrics()
        self.poll_timeout = poll_timeout
        self.lag_interval_s = lag_interval_s
        self._sleep = sleep
        self._before_commit = before_commit
        self._decoded: Optional[Event] = None

    # --- loop -------------------------------------------------------------------

    def run(
        self,
        *,
        max_messages: Optional[int] = None,
        idle_timeout_s: Optional[float] = None,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> int:
        from confluent_kafka import KafkaError, KafkaException

        self.consumer.subscribe(list(self.topics.values()))
        handled, last_message, last_lag = 0, time.monotonic(), 0.0
        log_event(_log, logging.INFO, "pipeline.lake_writer.started", topics=sorted(self.topics.values()),
                  store=self.store.backend)
        try:
            while not should_stop() and (max_messages is None or handled < max_messages):
                message = self.consumer.poll(self.poll_timeout)
                now = time.monotonic()
                if now - last_lag >= self.lag_interval_s:
                    self.update_lag()
                    last_lag = now
                if message is None:
                    if idle_timeout_s is not None and now - last_message >= idle_timeout_s:
                        break
                    continue
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF:
                        continue
                    self.metrics.inc("lake_writer_consumer_errors_total", code=error.name())
                    log_event(_log, logging.WARNING, "pipeline.lake_writer.consumer_error",
                              code=error.name(), error=error.str(), fatal=error.fatal())
                    if error.fatal():
                        raise KafkaException(error)
                    continue
                last_message = now
                self.handle(message)
                handled += 1
        finally:
            self.update_lag()
            log_event(_log, logging.INFO, "pipeline.metrics", component=self.COMPONENT, handled=handled,
                      metrics=self.metrics.snapshot())
        return handled

    def update_lag(self) -> None:
        try:
            lag = committed_lag(self.consumer, self.consumer.assignment())
        except Exception as exc:  # lag is advisory; a broker hiccup must not stop consumption
            log_event(_log, logging.WARNING, "pipeline.lake_writer.lag_unavailable",
                      error_type=type(exc).__name__, error=str(exc)[:200])
            return
        for (topic, partition), value in lag.items():
            self.metrics.set("lake_writer_consumer_lag", value, topic=topic, partition=partition)

    # --- one message ---------------------------------------------------------------

    def handle(self, message) -> HandleResult:
        started, first_failed_at = time.monotonic(), datetime.now(timezone.utc)
        topic = message.topic()
        self.metrics.inc("lake_writer_messages_consumed_total", topic=topic)
        attempts = 0

        def attempt() -> HandleResult:
            nonlocal attempts
            attempts += 1
            return self.process(message)

        self._decoded = None
        try:
            result = call_with_retry(attempt, operation="lake_writer.process", policy=self.policy,
                                     classify=_retryable, sleep=self._sleep)
        except (PermanentError, RetryExhausted) as exc:
            reason = failure_reason(exc).value
            self.metrics.inc("lake_writer_failures_total", reason=reason)
            result = self._dead_letter(message, exc, first_failed_at)
        finally:
            if attempts > 1:
                self.metrics.inc("lake_writer_retries_total", attempts - 1)
        self.metrics.observe("lake_writer_processing_seconds", time.monotonic() - started, topic=topic)
        if self._before_commit is not None:
            self._before_commit(message, result)
        self.commit(message)
        return result

    def process(self, message) -> HandleResult:
        topic, partition, offset = message.topic(), message.partition(), message.offset()
        value = message.value()
        if value is None:
            raise PermanentError(FailureReason.DESERIALIZE, "message has no value (tombstone)")
        event = decode_event(value, self.max_bytes)
        self._decoded = event
        check_event(event)
        key = (message.key() or b"").decode("utf-8", errors="replace")
        if key != event.event_id:
            raise PermanentError(FailureReason.ENVELOPE_INVALID, f"message key {key[:80]!r} is not the event_id")
        expected = self.topics.get(event.domain)
        if topic != expected:
            raise PermanentError(FailureReason.ENVELOPE_INVALID,
                                 f"{event.domain} event on topic {topic!r}; expected {expected!r}")
        try:
            object_key = raw_key_for(event)
        except InvalidKey as exc:
            raise PermanentError(FailureReason.ENVELOPE_INVALID, str(exc)) from exc
        uri = self.store.uri(object_key)

        with self._sessions() as db:
            record = repo.get_record(db, event.event_id)
            tracked = record is not None
            landed_at = record.raw_uri if record is not None else None
        if landed_at and landed_at != uri:
            raise PermanentError(
                FailureReason.INTEGRITY_CONFLICT,
                f"event {event.event_id} already landed at a different object",
                [{"existing_uri": landed_at, "new_uri": uri}],
            )

        put = self._put(object_key, encode_event(event, self.max_bytes))
        if not put.identical:
            raise PermanentError(
                FailureReason.INTEGRITY_CONFLICT,
                f"{put.uri} already holds different bytes for event {event.event_id}",
                [{"uri": put.uri, "stored_sha256": put.existing_sha256, "offered_sha256": put.sha256}],
            )

        if tracked:
            with self._sessions() as db:
                repo.record_landing(db, event.event_id, raw_uri=put.uri, topic=topic, partition=partition, offset=offset)
                db.commit()
        else:
            self.metrics.inc("lake_writer_untracked_total", topic=topic)
            log_event(_log, logging.WARNING, "pipeline.lake_writer.untracked_event", event_id=event.event_id,
                      topic=topic, partition=partition, offset=offset, raw_uri=put.uri)

        outcome = Outcome.LANDED if put.created else Outcome.REPLAY
        self.metrics.inc("lake_writer_lake_writes_total", result=outcome.value)
        log_event(_log, logging.INFO if put.created else logging.DEBUG, f"pipeline.lake_writer.{outcome.value}",
                  event_id=event.event_id, topic=topic, partition=partition, offset=offset, raw_uri=put.uri,
                  sha256=put.sha256, tracked=tracked)
        return HandleResult(outcome, topic, partition, offset, event.event_id, put.uri, tracked)

    def _put(self, key: str, data: bytes) -> PutResult:
        try:
            return self.store.put_if_absent(key, data, content_type="application/json")
        except InvalidKey as exc:
            raise PermanentError(FailureReason.ENVELOPE_INVALID, str(exc)) from exc
        except StoredBytesCorrupt as exc:
            raise PermanentError(FailureReason.INTEGRITY_CONFLICT, str(exc)) from exc
        except Exception as exc:
            if type(exc).__name__ == "ClientError" and not is_transient(exc):
                raise PermanentError(FailureReason.STORAGE_REJECTED, str(exc)[:500]) from exc
            raise

    def _dead_letter(self, message, error: BaseException, first_failed_at: datetime) -> HandleResult:
        topic, partition, offset = message.topic(), message.partition(), message.offset()
        reason = failure_reason(error).value
        event_id, source, run_id = self._identity(message)
        dlq_id = deterministic_dlq_id(self.COMPONENT, topic, partition, offset, reason)
        letter = dead_letter(
            component=self.COMPONENT, error=error, original_value=message.value() or b"",
            max_bytes=self.max_bytes, first_failed_at=first_failed_at, source=source, event_id=event_id,
            run_id=run_id, original_topic=topic, partition=partition, offset=offset,
            original_key=message.key(), dlq_id=dlq_id,
        )
        delivery = self.dlq.publish(letter)  # raises unless acknowledged → offset stays uncommitted
        self.metrics.inc("lake_writer_dlq_total", reason=reason)
        log_event(_log, logging.WARNING, "pipeline.dlq.published", component=self.COMPONENT, dlq_id=str(dlq_id),
                  reason=reason, topic=topic, partition=partition, offset=offset, event_id=event_id,
                  retry_count=letter.retry_count, error_type=letter.error_type,
                  error=letter.error_message[:300], dlq_partition=delivery.partition, dlq_offset=delivery.offset)
        if event_id:
            with self._sessions() as db:
                repo.advance_records(db, IngestionRecordStatus.DLQ, [event_id],
                                     reason=f"{reason}: {letter.error_message}")
                db.commit()
        return HandleResult(Outcome.DEAD_LETTERED, topic, partition, offset, event_id, dlq_id=dlq_id,
                            failure_reason=reason)

    def _identity(self, message):
        """Best-effort identity for the dead letter: the decoded event if decoding
        got that far, otherwise well-formed headers."""
        if self._decoded is not None:
            return self._decoded.event_id, self._decoded.source, self._decoded.run_id
        headers = {k: v for k, v in (message.headers() or []) if isinstance(v, (bytes, bytearray))}
        event_id = headers.get("event_id", b"").decode("ascii", errors="ignore")
        source = headers.get("source", b"").decode("utf-8", errors="ignore")[:64] or None
        try:
            run_id = uuid.UUID(headers.get("run_id", b"").decode("ascii", errors="ignore"))
        except ValueError:
            run_id = None
        return (event_id if _EVENT_ID.fullmatch(event_id) else None), source, run_id

    def commit(self, message) -> None:
        from confluent_kafka import KafkaException

        try:
            self.consumer.commit(message=message, asynchronous=False)
        except KafkaException as exc:
            # The work is durable; an uncommitted offset only means a harmless replay later.
            self.metrics.inc("lake_writer_commit_failures_total", topic=message.topic())
            log_event(_log, logging.WARNING, "pipeline.lake_writer.commit_failed", topic=message.topic(),
                      partition=message.partition(), offset=message.offset(), error=str(exc)[:300])


def _retryable(error: BaseException) -> bool:
    return not isinstance(error, RetryExhausted) and is_transient(error)

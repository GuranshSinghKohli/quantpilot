"""Publishing with delivery acknowledgement.

A message counts as delivered only when librdkafka's delivery report says so;
that report carries the partition and offset Kafka assigned. Retriable failures
are re-produced with the shared ``RetryPolicy`` backoff, which is at-least-once:
a re-produce after an ambiguous timeout may duplicate the message in Kafka, and
downstream consumers are idempotent for exactly that reason.
"""

from __future__ import annotations

import functools
import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, Union

from app.core.structured_logging import get_logger, log_event
from app.pipeline.contracts import DeadLetter
from app.pipeline.errors import PipelineError, RetryExhausted
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from app.pipeline.serde import encode_dead_letter

_log = get_logger("quantpilot.pipeline.producer")

Headers = Sequence[Tuple[str, bytes]]


def _retriable_codes():
    from confluent_kafka import KafkaError as E

    return {
        E._MSG_TIMED_OUT, E._TRANSPORT, E._ALL_BROKERS_DOWN, E._TIMED_OUT, E._QUEUE_FULL,
        E.REQUEST_TIMED_OUT, E.NOT_ENOUGH_REPLICAS, E.NOT_ENOUGH_REPLICAS_AFTER_APPEND,
        E.LEADER_NOT_AVAILABLE, E.NOT_LEADER_FOR_PARTITION,
    }


class DeliveryFailed(PipelineError):
    """Kafka did not acknowledge the message."""

    def __init__(self, code: str, message: str, *, retriable: bool, fatal: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retriable = retriable
        self.fatal = fatal

    @classmethod
    def from_kafka(cls, error) -> "DeliveryFailed":
        retriable = (error.retriable() or error.code() in _retriable_codes()) and not error.fatal()
        return cls(error.name(), error.str(), retriable=retriable, fatal=error.fatal())


@dataclass(frozen=True)
class OutboundMessage:
    topic: str
    key: str
    value: bytes
    headers: Headers = ()


@dataclass(frozen=True)
class Delivery:
    topic: str
    partition: int
    offset: int
    key: str
    attempts: int
    latency_seconds: float


@dataclass(frozen=True)
class PublishFailure:
    message: OutboundMessage
    error: BaseException  # DeliveryFailed, or RetryExhausted wrapping one
    attempts: int


Result = Union[Delivery, PublishFailure]


class EventProducer:
    def __init__(
        self,
        conf: dict,
        *,
        policy: RetryPolicy,
        metrics: Optional[Metrics] = None,
        producer=None,
        sleep: Callable[[float], None] = time.sleep,
        flush_timeout_s: Optional[float] = None,
    ) -> None:
        if producer is None:
            from confluent_kafka import Producer

            producer = Producer(conf)
        self._producer = producer
        self.policy = policy
        self.metrics = metrics or Metrics()
        self._sleep = sleep
        # librdkafka reports every message within delivery.timeout.ms, so a flush
        # that outlives it has seen a report for everything it sent.
        self._flush_timeout = flush_timeout_s or conf.get("delivery.timeout.ms", 30_000) / 1000 + 10

    def publish(self, message: OutboundMessage) -> Delivery:
        (result,) = self.publish_many([message])
        if isinstance(result, PublishFailure):
            raise result.error
        return result

    def publish_many(self, messages: Sequence[OutboundMessage]) -> List[Result]:
        results: List[Optional[Result]] = [None] * len(messages)
        started = time.monotonic()
        pending = list(range(len(messages)))
        attempt = 0
        while pending:
            attempt += 1
            reports = self._attempt([messages[i] for i in pending])
            retry = []
            for index, report in zip(pending, reports):
                message = messages[index]
                if isinstance(report, DeliveryFailed):
                    if report.retriable and attempt < self.policy.max_attempts:
                        retry.append(index)
                        continue
                    error: BaseException = (
                        RetryExhausted(f"kafka.produce:{message.topic}", attempt, report) if report.retriable else report
                    )
                    results[index] = PublishFailure(message, error, attempt)
                    self.metrics.inc("producer_failures_total", topic=message.topic, reason=report.code)
                    log_event(_log, logging.ERROR, "pipeline.produce.failed", topic=message.topic,
                              key=message.key, attempts=attempt, error_code=report.code, error=str(report)[:300])
                else:
                    topic, partition, offset = report
                    latency = time.monotonic() - started
                    results[index] = Delivery(topic, partition, offset, message.key, attempt, latency)
                    self.metrics.inc("producer_messages_acknowledged_total", topic=topic)
                    self.metrics.observe("producer_publish_latency_seconds", latency, topic=topic)
            if retry:
                wait = self.policy.delay(attempt)
                self.metrics.inc("producer_retries_total", len(retry))
                log_event(_log, logging.WARNING, "pipeline.produce.retry", attempt=attempt,
                          max_attempts=self.policy.max_attempts, messages=len(retry), delay_seconds=round(wait, 3))
                self._sleep(wait)
            pending = retry
        return results  # type: ignore[return-value]

    def _attempt(self, batch: Sequence[OutboundMessage]) -> list:
        from confluent_kafka import KafkaException

        reports: list = [None] * len(batch)
        for i, message in enumerate(batch):
            self.metrics.inc("producer_messages_attempted_total", topic=message.topic)
            callback = functools.partial(self._on_delivery, reports, i)
            while True:
                try:
                    self._producer.produce(
                        message.topic, key=message.key.encode("utf-8"), value=message.value,
                        headers=list(message.headers), on_delivery=callback,
                    )
                    break
                except BufferError:
                    self._producer.poll(0.5)  # local queue full: serve callbacks, then retry
                except KafkaException as exc:
                    reports[i] = DeliveryFailed.from_kafka(exc.args[0])
                    break
            self._producer.poll(0)
        self._producer.flush(self._flush_timeout)
        for i, report in enumerate(reports):
            if report is None:
                reports[i] = DeliveryFailed("_NO_DELIVERY_REPORT", "no delivery report before flush timeout", retriable=True)
        return reports

    @staticmethod
    def _on_delivery(reports: list, index: int, error, message) -> None:
        reports[index] = DeliveryFailed.from_kafka(error) if error is not None else (
            message.topic(), message.partition(), message.offset()
        )

    def close(self) -> int:
        """Flush outstanding messages. Returns how many were still undelivered."""
        remaining = self._producer.flush(self._flush_timeout)
        if remaining:
            log_event(_log, logging.ERROR, "pipeline.produce.unflushed", messages=remaining)
        return remaining


class DeadLetterPublisher:
    """Publishes dead letters (keyed by their deterministic ``dlq_id``) and
    raises unless Kafka acknowledged them."""

    def __init__(self, producer: EventProducer, topic: str) -> None:
        self.producer = producer
        self.topic = topic

    def publish(self, letter: DeadLetter) -> Delivery:
        headers = [
            ("dlq_id", str(letter.dlq_id).encode()),
            ("component", letter.component.encode()),
            ("failure_reason", letter.failure_reason.encode()),
        ]
        return self.producer.publish(OutboundMessage(self.topic, str(letter.dlq_id), encode_dead_letter(letter), headers))

import pytest
from confluent_kafka import KafkaError

from app.pipeline.dlq import dead_letter
from app.pipeline.errors import RetryExhausted
from app.pipeline.kafka.producer import (
    DeadLetterPublisher,
    Delivery,
    DeliveryFailed,
    EventProducer,
    OutboundMessage,
    PublishFailure,
)
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from tests.unit.pipeline.factories import INGESTED_AT
from tests.unit.pipeline.kafka_fakes import FakeProducer

POLICY = RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.02)


def producer(behaviour=None):
    fake, sleeps, metrics = FakeProducer(behaviour), [], Metrics()
    return EventProducer({}, policy=POLICY, metrics=metrics, producer=fake, sleep=sleeps.append), fake, sleeps, metrics


def msg(key="k1"):
    return OutboundMessage("test.t", key, b'{"v":1}', [("h", b"1")])


def test_ack_returns_kafka_assigned_position():
    p, fake, sleeps, metrics = producer()
    delivery = p.publish(msg())
    assert isinstance(delivery, Delivery)
    assert (delivery.topic, delivery.key, delivery.attempts) == ("test.t", "k1", 1)
    assert (delivery.partition, delivery.offset) == (fake.produced[0].partition(), 0)
    assert fake.produced[0].key() == b"k1" and fake.produced[0].headers() == [("h", b"1")]
    assert metrics.total("producer_messages_acknowledged_total") == 1 and sleeps == []


def test_retriable_failure_is_retried_with_backoff():
    timeout = KafkaError(KafkaError._MSG_TIMED_OUT)
    p, fake, sleeps, metrics = producer(lambda t, k, attempt: timeout if attempt == 1 else None)
    delivery = p.publish(msg())
    assert delivery.attempts == 2 and len(sleeps) == 1
    assert metrics.total("producer_retries_total") == 1
    assert metrics.total("producer_messages_attempted_total") == 2


def test_retries_are_bounded():
    p, fake, sleeps, metrics = producer(lambda t, k, a: KafkaError(KafkaError._TRANSPORT))
    (result,) = p.publish_many([msg()])
    assert isinstance(result, PublishFailure) and isinstance(result.error, RetryExhausted)
    assert result.attempts == POLICY.max_attempts and len(sleeps) == POLICY.max_attempts - 1
    assert fake.produced == []
    with pytest.raises(RetryExhausted):
        p.publish(msg("k2"))


def test_permanent_failure_is_not_retried():
    p, fake, sleeps, _ = producer(lambda t, k, a: KafkaError(KafkaError.MSG_SIZE_TOO_LARGE))
    with pytest.raises(DeliveryFailed) as info:
        p.publish(msg())
    assert not info.value.retriable and sleeps == []


def test_missing_delivery_report_is_not_success():
    p, fake, sleeps, _ = producer(lambda t, k, attempt: "no-report" if attempt == 1 else None)
    assert p.publish(msg()).attempts == 2


def test_batch_mixes_outcomes_without_losing_any():
    failing = KafkaError(KafkaError.MSG_SIZE_TOO_LARGE)
    p, fake, _, _ = producer(lambda t, key, a: failing if key == b"bad" else None)
    results = p.publish_many([msg("a"), msg("bad"), msg("b")])
    assert [type(r).__name__ for r in results] == ["Delivery", "PublishFailure", "Delivery"]
    assert [m.key() for m in fake.produced] == [b"a", b"b"]


def test_buffer_full_waits_then_produces():
    fake = FakeProducer()
    original, calls = fake.produce, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise BufferError("queue full")
        return original(*args, **kwargs)

    fake.produce = flaky
    p = EventProducer({}, policy=POLICY, producer=fake, sleep=lambda s: None)
    assert p.publish(msg()).attempts == 1 and len(calls) == 2


def test_dead_letter_publisher_keys_by_dlq_id():
    p, fake, _, _ = producer()
    letter = dead_letter(component="lake_writer", error=ValueError("x"), original_value=b"{}",
                         max_bytes=1_000_000, first_failed_at=INGESTED_AT)
    DeadLetterPublisher(p, "test.dlq").publish(letter)
    (sent,) = fake.on("test.dlq")
    assert sent.key() == str(letter.dlq_id).encode()
    assert dict(sent.headers())["failure_reason"] == b"payload_invalid"

"""Publisher + lake writer against fake Kafka clients, a tmp local lake and a
migrated tmp SQLite. The same flows run against a real broker in tests/kafka/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from confluent_kafka import KafkaError

from app.domain.enums import IngestionRecordStatus as S
from app.domain.enums import IngestionRunStatus
from app.pipeline import synthetic
from app.pipeline.kafka.producer import EventProducer
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake.keys import raw_key_for
from app.pipeline.lake_writer import Outcome
from app.pipeline.metrics import Metrics
from app.pipeline.serde import decode_dead_letter, encode_event, event_headers
from app.repositories import ingestion as repo
from tests.unit.pipeline.kafka_fakes import FakeConsumer, FakeMessage, FakeProducer
from tests.unit.pipeline.pipeline_env import DLQ_TOPIC, MAX_BYTES, POLICY, TOPICS, Wiring, migrated_sessions

T = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(tmp_path):
    fake, metrics, sleeps = FakeProducer(), Metrics(), []
    producer = EventProducer({}, policy=POLICY, metrics=metrics, producer=fake, sleep=sleeps.append)
    wiring = Wiring(migrated_sessions(tmp_path / "p.db"), LocalObjectStore(tmp_path / "lake"), producer, metrics,
                    TOPICS, DLQ_TOPIC, sleeps)
    wiring.fake = fake
    return wiring


def record(env, event_id):
    with env.sessions() as db:
        return repo.get_record(db, event_id)


def raw_message(event, *, topic=TOPICS["market"], partition=0, offset=0, key=None, value=None):
    return FakeMessage(topic, partition, offset, (key or event.event_id).encode(),
                       value if value is not None else encode_event(event, MAX_BYTES), event_headers(event))


def dead_letters(env):
    return [decode_dead_letter(m.value()) for m in env.fake.on(DLQ_TOPIC)]


# --- publisher -------------------------------------------------------------------


def test_publisher_marks_published_only_on_ack_and_keys_by_event_id(env):
    tracker, events, summary = env.publish_synthetic(3)
    assert (summary.published, summary.duplicate, summary.publish_failed) == (3, 0, 0)
    sent = env.fake.on(TOPICS["market"])
    assert [m.key().decode() for m in sent] == [e.event_id for e in events]
    assert dict(sent[0].headers())["correlation_id"] == str(tracker.run_id).encode()
    for message in sent:
        r = record(env, message.key().decode())
        assert (r.status, r.kafka_topic, r.kafka_partition, r.kafka_offset) == (
            S.PUBLISHED.value, TOPICS["market"], message.partition(), message.offset())
    assert tracker.finish() is IngestionRunStatus.COMPLETED


def test_unacknowledged_events_are_publish_failed_then_retried(env):
    env.fake.behaviour = lambda t, k, a: KafkaError(KafkaError._ALL_BROKERS_DOWN)
    tracker, events, summary = env.publish_synthetic(2)
    assert (summary.published, summary.publish_failed) == (0, 2)
    assert {record(env, e.event_id).status for e in events} == {S.PUBLISH_FAILED.value}
    assert tracker.finish() is IngestionRunStatus.FAILED

    env.fake.behaviour = lambda t, k, a: None
    tracker, events, summary = env.publish_synthetic(2)
    assert (summary.published, summary.duplicate) == (2, 0)  # RETRY claims publish again
    assert {record(env, e.event_id).status for e in events} == {S.PUBLISHED.value}


def test_already_delivered_events_are_skipped(env):
    env.publish_synthetic(2)
    tracker, events, summary = env.publish_synthetic(3)
    assert (summary.published, summary.duplicate) == (1, 2)
    assert len(env.fake.on(TOPICS["market"])) == 3
    assert tracker.finish() is IngestionRunStatus.COMPLETED


def test_duplicates_within_one_batch_are_published_once(env):
    tracker = env.start_run()
    events = synthetic.market_events(1, run_id=tracker.run_id) * 2
    summary = env.publisher().publish(tracker, events)
    assert (summary.published, summary.duplicate) == (1, 1)


def test_oversize_event_is_dead_lettered_by_the_ingestor(env):
    tracker = env.start_run()
    (event,) = synthetic.market_events(1, run_id=tracker.run_id)
    publisher = env.publisher()
    publisher.max_bytes = 100
    summary = publisher.publish(tracker, [event])
    assert summary.dead_lettered == 1 and env.fake.on(TOPICS["market"]) == []
    (letter,) = dead_letters(env)
    assert (letter.component, letter.failure_reason, letter.event_id) == ("ingestor", "oversize", event.event_id)
    assert record(env, event.event_id).status == S.DLQ.value


# --- lake writer: success and replay ---------------------------------------------


def test_consumed_event_lands_and_records_lineage_before_commit(env):
    _, events, _ = env.publish_synthetic(3)
    consumer = FakeConsumer(env.fake.on(TOPICS["market"]))
    commits_seen_at_hook = []
    writer = env.writer(consumer, before_commit=lambda m, r: commits_seen_at_hook.append(dict(consumer.committed_offsets)))
    assert writer.run(max_messages=3) == 3
    assert commits_seen_at_hook[0] == {}  # nothing committed while processing the first message
    for event, message in zip(events, env.fake.on(TOPICS["market"])):
        r = record(env, event.event_id)
        key = raw_key_for(event)
        assert (r.status, r.raw_uri) == (S.LANDED.value, env.store.uri(key))
        assert json.loads(env.store.get(key))["event_id"] == event.event_id
        assert consumer.committed_offsets[(message.topic(), message.partition())] >= message.offset() + 1
    assert env.metrics.value("lake_writer_lake_writes_total", result="landed") == 3


def test_redelivery_is_an_idempotent_replay(env):
    _, (event,), _ = env.publish_synthetic(1)
    (message,) = env.fake.on(TOPICS["market"])
    writer = env.writer(FakeConsumer([message, message]))
    writer.run(max_messages=2)
    assert env.metrics.value("lake_writer_lake_writes_total", result="replay") == 1
    assert [i.key for i in env.store.list("raw/")] == [raw_key_for(event)]
    assert record(env, event.event_id).status == S.LANDED.value


def test_crash_after_lake_write_before_commit_is_safe_on_redelivery(env):
    _, (event,), _ = env.publish_synthetic(1)
    (message,) = env.fake.on(TOPICS["market"])

    class Crash(Exception):
        pass

    def crash(msg, result):
        raise Crash()

    first = FakeConsumer([message])
    with pytest.raises(Crash):
        env.writer(first, before_commit=crash).run()
    assert first.committed_offsets == {}
    assert env.store.exists(raw_key_for(event))

    second = FakeConsumer([message])  # the broker redelivers the uncommitted message
    result = env.writer(second).handle(message)
    assert result.outcome is Outcome.REPLAY
    assert second.committed_offsets == {(message.topic(), message.partition()): message.offset() + 1}
    assert record(env, event.event_id).status == S.LANDED.value
    assert len(list(env.store.list("raw/"))) == 1


def test_landed_record_never_regresses(env):
    _, (event,), _ = env.publish_synthetic(1)
    (message,) = env.fake.on(TOPICS["market"])
    env.writer(FakeConsumer()).handle(message)
    with env.sessions() as db:
        assert repo.record_deliveries(db, [(event.event_id, 9, 999)]) == 0
        db.commit()
    r = record(env, event.event_id)
    assert (r.status, r.kafka_partition) == (S.LANDED.value, message.partition())


def test_untracked_event_still_lands(env):
    (event,) = synthetic.market_events(1, run_id=uuid.uuid4(), ingested_at=T)
    result = env.writer(FakeConsumer()).handle(raw_message(event))
    assert (result.outcome, result.tracked) == (Outcome.LANDED, False)
    assert env.metrics.total("lake_writer_untracked_total") == 1


# --- lake writer: failures -------------------------------------------------------


def test_conflicting_replay_is_detected_and_never_overwrites(env):
    first, second = (synthetic.market_events(1, run_id=uuid.uuid4(), ingested_at=T)[0] for _ in range(2))
    assert first.event_id == second.event_id and encode_event(first, MAX_BYTES) != encode_event(second, MAX_BYTES)
    writer = env.writer(FakeConsumer())
    assert writer.handle(raw_message(first, offset=0)).outcome is Outcome.LANDED
    result = writer.handle(raw_message(second, offset=1))
    assert (result.outcome, result.failure_reason) == (Outcome.DEAD_LETTERED, "integrity_conflict")
    assert env.store.get(raw_key_for(first)) == encode_event(first, MAX_BYTES)
    (letter,) = dead_letters(env)
    assert letter.error_details[0]["stored_sha256"] != letter.error_details[0]["offered_sha256"]


def test_same_event_at_a_different_object_is_a_conflict(env):
    _, (event,), _ = env.publish_synthetic(1, ingested_at=T)
    (message,) = env.fake.on(TOPICS["market"])
    writer = env.writer(FakeConsumer())
    writer.handle(message)
    later = event.model_copy(update={"ingested_at": event.ingested_at + timedelta(days=1)})
    result = writer.handle(raw_message(later, offset=5))
    assert result.failure_reason == "integrity_conflict"
    assert not env.store.exists(raw_key_for(later))
    assert record(env, event.event_id).status == S.LANDED.value  # dlq cannot regress landed


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda e, m: raw_message(e, value=b"not json"), "deserialize"),
        (lambda e, m: raw_message(e, value=m.value().replace(b'"schema_version":1', b'"schema_version":99', 1)),
         "unsupported_schema_version"),
        (lambda e, m: raw_message(e, value=m.value().replace(b'"volume":10000', b'"volume":10001', 1)), "hash_mismatch"),
        (lambda e, m: raw_message(e, key="0" * 64), "envelope_invalid"),
        (lambda e, m: raw_message(e, topic=TOPICS["news"]), "envelope_invalid"),
        (lambda e, m: FakeMessage(TOPICS["market"], 0, 0, None, None), "deserialize"),
    ],
)
def test_permanent_failures_go_to_the_dlq_and_are_committed(env, mutate, reason):
    _, (event,), _ = env.publish_synthetic(1)
    (published,) = env.fake.on(TOPICS["market"])
    message = mutate(event, published)
    consumer = FakeConsumer()
    result = env.writer(consumer).handle(message)
    assert (result.outcome, result.failure_reason) == (Outcome.DEAD_LETTERED, reason)
    (letter,) = dead_letters(env)
    assert (letter.component, letter.failure_reason, letter.original_topic, letter.partition, letter.offset) == (
        "lake_writer", reason, message.topic(), message.partition(), message.offset())
    assert letter.retry_count == 0 and env.sleeps == []
    assert consumer.committed_offsets == {(message.topic(), message.partition()): message.offset() + 1}
    assert list(env.store.list("raw/")) == []


class FlakyStore(LocalObjectStore):
    def __init__(self, root, failures):
        super().__init__(root)
        self.failures = failures

    def put_if_absent(self, key, data, *, content_type="application/octet-stream"):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("storage temporarily unreachable")
        return super().put_if_absent(key, data, content_type=content_type)


def test_transient_failure_is_retried_then_succeeds(env, tmp_path):
    _, (event,), _ = env.publish_synthetic(1)
    (message,) = env.fake.on(TOPICS["market"])
    result = env.writer(FakeConsumer(), store=FlakyStore(tmp_path / "lake", 1)).handle(message)
    assert result.outcome is Outcome.LANDED and len(env.sleeps) == 1
    assert env.metrics.total("lake_writer_retries_total") == 1
    assert dead_letters(env) == []


def test_retry_exhaustion_goes_to_the_dlq(env, tmp_path):
    _, (event,), _ = env.publish_synthetic(1)
    (message,) = env.fake.on(TOPICS["market"])
    consumer = FakeConsumer()
    result = env.writer(consumer, store=FlakyStore(tmp_path / "lake", 99)).handle(message)
    assert result.failure_reason == "retries_exhausted"
    (letter,) = dead_letters(env)
    assert (letter.retry_count, letter.event_id, letter.error_type) == (POLICY.max_attempts - 1, event.event_id,
                                                                       "ConnectionError")
    assert record(env, event.event_id).status == S.DLQ.value
    assert consumer.committed_offsets  # the failure is recorded, so the partition moves on


def test_dlq_publish_failure_leaves_the_offset_uncommitted(env):
    (event,) = synthetic.market_events(1, run_id=uuid.uuid4(), ingested_at=T)
    env.fake.behaviour = lambda t, k, a: KafkaError(KafkaError._ALL_BROKERS_DOWN) if t == DLQ_TOPIC else None
    consumer = FakeConsumer()
    with pytest.raises(Exception):
        env.writer(consumer).handle(raw_message(event, value=b"{broken"))
    assert consumer.committed_offsets == {}


def test_unexpected_errors_propagate_without_commit_or_dlq(env, tmp_path):
    class Broken(LocalObjectStore):
        def put_if_absent(self, *a, **k):
            raise RuntimeError("bug")

    (event,) = synthetic.market_events(1, run_id=uuid.uuid4(), ingested_at=T)
    consumer = FakeConsumer()
    with pytest.raises(RuntimeError):
        env.writer(consumer, store=Broken(tmp_path / "x")).handle(raw_message(event))
    assert consumer.committed_offsets == {} and dead_letters(env) == []


def test_redelivered_failure_reuses_the_dead_letter_id(env):
    (event,) = synthetic.market_events(1, run_id=uuid.uuid4(), ingested_at=T)
    bad = raw_message(event, value=b"nope", offset=7)
    env.writer(FakeConsumer()).handle(bad)
    env.writer(FakeConsumer()).handle(bad)
    first, second = dead_letters(env)
    assert first.dlq_id == second.dlq_id


def test_metrics_render_in_prometheus_format(env):
    env.publish_synthetic(1)
    env.writer(FakeConsumer(env.fake.on(TOPICS["market"]))).run(max_messages=1)
    text = env.metrics.render_prometheus()
    assert "# TYPE quantpilot_producer_messages_acknowledged_total counter" in text
    assert 'quantpilot_lake_writer_lake_writes_total{result="landed"} 1' in text
    assert "quantpilot_lake_writer_processing_seconds_count" in text

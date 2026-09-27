"""Publisher → Kafka → lake writer → local lake → ingestion_records, on a real
broker. SYNTHETIC events only; every resource is per-test (see conftest)."""

from __future__ import annotations

import uuid

import pytest
from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition

from app.domain.enums import IngestionRecordStatus as S
from app.pipeline import synthetic
from app.pipeline.kafka.config import consumer_config, producer_config
from app.pipeline.kafka.consumer import OFFSET_UNSET, build_consumer
from app.pipeline.kafka.producer import DeadLetterPublisher, EventProducer, OutboundMessage
from app.pipeline.kafka.topics import TopicSpec, ensure_topics, verify_topic
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake.keys import raw_key_for
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from app.pipeline.serde import decode_dead_letter, encode_event, event_headers
from app.repositories import ingestion as repo
from tests.unit.pipeline.pipeline_env import MAX_BYTES, Wiring, migrated_sessions
from tests.unit.pipeline.test_lake_writer_and_publisher import FlakyStore


@pytest.fixture()
def env(kafka_settings, topics, tmp_path):
    metrics = Metrics()
    producer = EventProducer(producer_config(kafka_settings), policy=RetryPolicy.from_settings(kafka_settings),
                             metrics=metrics)
    wiring = Wiring(migrated_sessions(tmp_path / "k.db"), LocalObjectStore(tmp_path / "lake"), producer, metrics,
                    kafka_settings.raw_topics, kafka_settings.KAFKA_TOPIC_DLQ, [])
    wiring.settings = kafka_settings
    yield wiring
    producer.close()


def lake_consumer(env):
    return build_consumer(env.settings, env.settings.KAFKA_CONSUMER_GROUP_LAKE)


def run_writer(env, expected, **kwargs):
    consumer = lake_consumer(env)
    writer = env.writer(consumer, **{k: v for k, v in kwargs.items() if k in ("store", "before_commit")})
    try:
        handled = writer.run(max_messages=expected, idle_timeout_s=30)
    finally:
        consumer.close()
    assert handled == expected
    return writer


def read_topic(env, topic, expected, timeout_polls=40):
    """Everything on ``topic`` from the beginning, without joining the lake group."""
    reader = Consumer(consumer_config(env.settings, f"{env.settings.KAFKA_CONSUMER_GROUP_LAKE}.reader.{uuid.uuid4().hex[:6]}"))
    try:
        partitions = reader.list_topics(topic, timeout=10).topics[topic].partitions
        reader.assign([TopicPartition(topic, p, OFFSET_BEGINNING) for p in partitions])
        out, polls = [], 0
        while len(out) < expected and polls < timeout_polls:
            message = reader.poll(0.5)
            polls += 1
            if message is not None and not message.error():
                out.append(message)
        return out
    finally:
        reader.close()


def committed(env, topic):
    probe = Consumer(consumer_config(env.settings, env.settings.KAFKA_CONSUMER_GROUP_LAKE))
    try:
        partitions = probe.list_topics(topic, timeout=10).topics[topic].partitions
        tps = probe.committed([TopicPartition(topic, p) for p in partitions], timeout=10)
        marks = {tp.partition: probe.get_watermark_offsets(tp, timeout=10)[1] for tp in tps}
        return {tp.partition: tp.offset for tp in tps}, marks
    finally:
        probe.close()


def assert_fully_committed(env, topic):
    """Every message on every partition is committed (empty partitions have nothing to commit)."""
    offsets, marks = committed(env, topic)
    assert {p: (o if o >= 0 else 0) for p, o in offsets.items()} == marks
    return offsets


def assert_fully_committed(env, topic):
    """Every message on every partition is committed (empty partitions have nothing to commit)."""
    offsets, marks = committed(env, topic)
    assert {p: (o if o >= 0 else 0) for p, o in offsets.items()} == marks
    return offsets


def assert_fully_committed(env, topic):
    """Every message on every partition is committed (empty partitions have nothing to commit)."""
    offsets, marks = committed(env, topic)
    assert {p: (o if o >= 0 else 0) for p, o in offsets.items()} == marks
    return offsets


def record(env, event_id):
    with env.sessions() as db:
        return repo.get_record(db, event_id)


def market_topic(env):
    return env.settings.KAFKA_TOPIC_MARKET_RAW


# --- topics ------------------------------------------------------------------------


def test_topics_are_created_deterministically_and_verified(kafka_settings, admin, topics):
    assert {r["action"] for r in topics["reports"]} == {"created"}
    again = ensure_topics(admin, topics["specs"])
    assert all(r["action"] == "verified" and r["problems"] == [] for r in again)
    metadata = admin.list_topics(timeout=10).topics
    for spec in topics["specs"]:
        assert len(metadata[spec.name].partitions) == spec.partitions
    wanted = {s.name: s.partitions for s in topics["specs"]}
    assert wanted[kafka_settings.KAFKA_TOPIC_MARKET_RAW] == 3 and wanted[kafka_settings.KAFKA_TOPIC_DLQ] == 1

    drifted = topics["specs"][0]
    wrong = TopicSpec(drifted.name, drifted.purpose, drifted.partitions + 1, drifted.replication_factor,
                      drifted.retention_ms * 2, drifted.max_message_bytes)
    problems = verify_topic(admin, wrong)
    assert any("partitions" in p for p in problems) and any("retention.ms" in p for p in problems)


# --- happy path ------------------------------------------------------------------


def test_publish_consume_land_record_commit(env):
    tracker, events, summary = env.publish_synthetic(6)
    assert summary.published == 6 and env.metrics.total("producer_messages_acknowledged_total") == 6

    on_topic = read_topic(env, market_topic(env), 6)
    assert sorted(m.key().decode() for m in on_topic) == sorted(e.event_id for e in events)
    for message in on_topic:
        r = record(env, message.key().decode())
        assert (r.status, r.kafka_partition, r.kafka_offset) == (S.PUBLISHED.value, message.partition(), message.offset())
    assert len({m.partition() for m in on_topic}) > 1  # event_id keys spread over the 3 partitions

    writer = run_writer(env, 6)
    for event in events:
        r = record(env, event.event_id)
        assert (r.status, r.raw_uri) == (S.LANDED.value, env.store.uri(raw_key_for(event)))
        assert env.store.get(raw_key_for(event)) == encode_event(event, MAX_BYTES)
    assert_fully_committed(env, market_topic(env))  # everything processed is committed, nothing more
    assert writer.metrics.value("lake_writer_lake_writes_total", result="landed") == 6


# --- the crash-before-commit replay ----------------------------------------------


def test_crash_after_lake_write_before_offset_commit_replays_safely(env):
    _, (event,), _ = env.publish_synthetic(1)

    class Crash(Exception):
        pass

    def crash(message, result):
        assert result.outcome.value == "landed"
        raise Crash("process died before committing")

    with pytest.raises(Crash):
        run_writer(env, 1, before_commit=crash)
    offsets, marks = committed(env, market_topic(env))
    assert set(offsets.values()) == {OFFSET_UNSET}
    assert env.store.exists(raw_key_for(event))
    assert record(env, event.event_id).status == S.LANDED.value

    writer = run_writer(env, 1)  # new consumer, same group: the message comes back
    assert writer.metrics.value("lake_writer_lake_writes_total", result="replay") == 1
    assert [i.key for i in env.store.list("raw/")] == [raw_key_for(event)]
    r = record(env, event.event_id)
    assert (r.status, r.raw_uri) == (S.LANDED.value, env.store.uri(raw_key_for(event)))
    assert_fully_committed(env, market_topic(env))


# --- duplicates and conflicts ------------------------------------------------------


def test_duplicate_delivery_writes_one_object(env):
    _, (event,), _ = env.publish_synthetic(1)
    env.producer.publish(OutboundMessage(market_topic(env), event.event_id, encode_event(event, MAX_BYTES),
                                         event_headers(event)))
    writer = run_writer(env, 2)
    assert writer.metrics.value("lake_writer_lake_writes_total", result="landed") == 1
    assert writer.metrics.value("lake_writer_lake_writes_total", result="replay") == 1
    assert len(list(env.store.list("raw/"))) == 1


def test_conflicting_replay_is_dead_lettered_and_the_first_copy_kept(env):
    _, (event,), _ = env.publish_synthetic(1)
    impostor = event.model_copy(update={"run_id": uuid.uuid4(), "correlation_id": "other-run"})
    env.producer.publish(OutboundMessage(market_topic(env), impostor.event_id, encode_event(impostor, MAX_BYTES),
                                         event_headers(impostor)))
    run_writer(env, 2)
    (dead,) = read_topic(env, env.settings.KAFKA_TOPIC_DLQ, 1)
    letter = decode_dead_letter(dead.value())
    assert (letter.failure_reason, letter.event_id, letter.original_topic) == (
        "integrity_conflict", event.event_id, market_topic(env))
    assert dead.key().decode() == str(letter.dlq_id)
    assert env.store.get(raw_key_for(event)) == encode_event(event, MAX_BYTES)
    assert record(env, event.event_id).status == S.LANDED.value


# --- DLQ and retries -----------------------------------------------------------------


def test_invalid_message_reaches_the_dlq_with_its_coordinates(env):
    delivery = env.producer.publish(OutboundMessage(market_topic(env), "not-an-event", b"garbage", []))
    run_writer(env, 1)
    (dead,) = read_topic(env, env.settings.KAFKA_TOPIC_DLQ, 1)
    letter = decode_dead_letter(dead.value())
    assert (letter.failure_reason, letter.component, letter.original_value) == ("deserialize", "lake_writer", "garbage")
    assert (letter.partition, letter.offset, letter.original_key) == (delivery.partition, delivery.offset, "not-an-event")
    offsets, marks = committed(env, market_topic(env))
    assert offsets[delivery.partition] == delivery.offset + 1


def test_transient_failures_retry_and_exhaustion_dead_letters(env, tmp_path):
    _, (event,), _ = env.publish_synthetic(1)
    writer = run_writer(env, 1, store=FlakyStore(tmp_path / "lake", failures=1))
    assert record(env, event.event_id).status == S.LANDED.value
    assert writer.metrics.total("lake_writer_retries_total") == 1

    _, events, _ = env.publish_synthetic(2)  # one new event (the first is a duplicate claim)
    new = [e for e in events if e.event_id != event.event_id]
    run_writer(env, 1, store=FlakyStore(tmp_path / "lake", failures=99))
    (dead,) = read_topic(env, env.settings.KAFKA_TOPIC_DLQ, 1)
    letter = decode_dead_letter(dead.value())
    assert (letter.failure_reason, letter.retry_count, letter.event_id) == (
        "retries_exhausted", env.settings.PIPELINE_RETRY_MAX_ATTEMPTS - 1, new[0].event_id)
    assert record(env, new[0].event_id).status == S.DLQ.value


def test_dlq_unavailable_leaves_the_offset_uncommitted(env):
    env.producer.publish(OutboundMessage(market_topic(env), "k", b"garbage", []))
    consumer = lake_consumer(env)
    writer = env.writer(consumer)
    writer.dlq = DeadLetterPublisher(env.producer, f"{env.settings.KAFKA_TOPIC_DLQ}.missing")
    try:
        with pytest.raises(Exception):
            writer.run(max_messages=1, idle_timeout_s=30)
    finally:
        consumer.close()
    offsets, _ = committed(env, market_topic(env))
    assert set(offsets.values()) == {OFFSET_UNSET}


def test_unreachable_broker_never_marks_events_published(kafka_settings, tmp_path):
    settings = kafka_settings.model_copy(update={"KAFKA_BOOTSTRAP_SERVERS": "127.0.0.1:9",
                                                 "KAFKA_DELIVERY_TIMEOUT_MS": 1_000})
    producer = EventProducer(producer_config(settings), policy=RetryPolicy(2, 0.05, 0.1))
    env = Wiring(migrated_sessions(tmp_path / "u.db"), LocalObjectStore(tmp_path / "lake"), producer, Metrics(),
                 settings.raw_topics, settings.KAFKA_TOPIC_DLQ, [])
    _, events, summary = env.publish_synthetic(2)
    assert (summary.published, summary.publish_failed) == (0, 2)
    assert {record(env, e.event_id).status for e in events} == {S.PUBLISH_FAILED.value}

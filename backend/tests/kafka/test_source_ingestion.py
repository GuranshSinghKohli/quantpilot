"""End to end with a mocked provider: mock Massive/SEC responses → adapter →
IngestionRunner → real Kafka → lake writer → MinIO → ingestion_records.

Needs TEST_KAFKA_BOOTSTRAP (this tier) and TEST_S3_ENDPOINT + credentials (the
s3 tier's MinIO); skipped without the latter. Topics, groups and the bucket are
per-test and removed afterwards. No real provider or credential is used.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
import pytest

from app.domain.enums import IngestionRecordStatus as S
from app.domain.enums import IngestionRunStatus as R
from app.pipeline.ingest import IngestionRunner
from app.pipeline.kafka.config import producer_config
from app.pipeline.kafka.consumer import build_consumer
from app.pipeline.kafka.producer import DeadLetterPublisher, EventProducer
from app.pipeline.lake.keys import raw_key_for
from app.pipeline.lake.s3 import S3ObjectStore, make_client
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from app.pipeline.serde import decode_dead_letter, decode_event
from app.pipeline.sources.registry import Wiring as SourceWiring
from app.pipeline.sources.registry import build_adapter
from app.repositories import ingestion as repo
from tests.kafka.test_kafka_pipeline import read_topic
from tests.s3.conftest import TEST_BUCKET_PREFIX, TEST_S3_ENDPOINT, _drop_bucket
from tests.unit.pipeline.pipeline_env import MAX_BYTES, Wiring, migrated_sessions
from tests.unit.pipeline.source_harness import (
    NEWS_PATH,
    SEC_PATH,
    T,
    FakeClock,
    MockProvider,
    article,
    news_body,
    next_news,
    ok,
    request,
    sec_body,
    source_settings,
)

pytestmark = pytest.mark.skipif(not TEST_S3_ENDPOINT, reason="TEST_S3_ENDPOINT not set (MinIO)")


@pytest.fixture()
def minio_store():
    key_id = os.getenv("TEST_S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("TEST_S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if not (key_id and secret):
        pytest.fail("TEST_S3_ENDPOINT is set but TEST_S3_ACCESS_KEY_ID / TEST_S3_SECRET_ACCESS_KEY are not")
    base = make_client(endpoint_url=TEST_S3_ENDPOINT, region="us-east-1")
    client = boto3.client("s3", endpoint_url=TEST_S3_ENDPOINT, region_name="us-east-1", aws_access_key_id=key_id,
                          aws_secret_access_key=secret, config=base.meta.config)
    bucket = f"{TEST_BUCKET_PREFIX}{uuid.uuid4().hex[:20]}"
    store = S3ObjectStore(bucket, client=client, endpoint_url=TEST_S3_ENDPOINT,
                          policy=RetryPolicy(max_attempts=5, base_delay=0.05, max_delay=0.5))
    store.ensure_bucket()
    try:
        yield store
    finally:
        _drop_bucket(client, bucket)


@pytest.fixture()
def e2e(kafka_settings, topics, tmp_path, minio_store):
    metrics = Metrics()
    producer = EventProducer(producer_config(kafka_settings), policy=RetryPolicy.from_settings(kafka_settings),
                             metrics=metrics)
    wiring = Wiring(migrated_sessions(tmp_path / "e2e.db"), minio_store, producer, metrics,
                    kafka_settings.raw_topics, kafka_settings.KAFKA_TOPIC_DLQ, [])
    wiring.settings = kafka_settings
    wiring.provider = MockProvider()
    yield wiring
    producer.close()


def ingest(env, domain, req):
    clock = FakeClock()
    adapter = build_adapter(domain, source_settings(), SourceWiring(transport=env.provider.transport(),
                                                                    sleep=clock.sleep, clock=clock))
    runner = IngestionRunner(adapter=adapter, publisher=env.publisher(),
                             dlq=DeadLetterPublisher(env.producer, env.dlq_topic), session_factory=env.sessions,
                             max_bytes=MAX_BYTES, metrics=env.metrics, trigger="test", now=lambda: T)
    try:
        return runner.run(req)
    finally:
        adapter.close()


def drain(env, expected):
    consumer = build_consumer(env.settings, env.settings.KAFKA_CONSUMER_GROUP_LAKE)
    writer = env.writer(consumer)
    try:
        assert writer.run(max_messages=expected, idle_timeout_s=30) == expected
    finally:
        consumer.close()


def test_mock_news_provider_to_minio_with_tracking_and_idempotent_rerun(e2e):
    page1 = [article(1), article(2), {"id": "missing-everything"}]
    page2 = [article(3), article(1)]  # article 1 repeats across pages
    e2e.provider.on(NEWS_PATH, ok(news_body(page1, next_news("p2"))), ok(news_body(page2)))
    req = request(symbols=("AAPL",))

    first = ingest(e2e, "news", req)
    assert (first.status, first.received, first.valid, first.invalid) == (R.PARTIAL, 5, 4, 1)
    assert (first.published, first.duplicate, first.dead_lettered_invalid) == (3, 1, 1)
    drain(e2e, 3)

    news_topic = e2e.settings.raw_topics["news"]
    messages = read_topic(e2e, news_topic, 3)
    events = [decode_event(m.value(), MAX_BYTES) for m in messages]
    ids = sorted(e.event_id for e in events)
    assert len(set(ids)) == 3 and all(m.key().decode() == e.event_id for m, e in zip(messages, events))

    objects = list(e2e.store.list("raw/news/"))
    assert len(objects) == 3
    with e2e.sessions() as db:
        for event in events:
            key = raw_key_for(event)
            stored = json.loads(e2e.store.get(key))
            # The raw layer is the provider's article verbatim, plus the envelope.
            assert stored["original"] == next(a for a in page1 + page2 if a.get("id") == event.external_id)
            assert {"insights", "keywords", "publisher"} <= set(stored["original"])
            record = repo.get_record(db, event.event_id)
            assert record.status == S.LANDED.value and record.raw_uri == e2e.store.uri(key)
            assert record.kafka_topic == news_topic and record.kafka_offset is not None

    (letter,) = [decode_dead_letter(m.value()) for m in read_topic(e2e, e2e.dlq_topic, 1)]
    assert (letter.component, letter.failure_reason, letter.source) == ("ingestor", "payload_invalid", "massive.news")

    # Same provider responses again: same event ids, nothing new anywhere.
    e2e.provider.on(NEWS_PATH, ok(news_body(page1, next_news("p2"))), ok(news_body(page2)))
    second = ingest(e2e, "news", req)
    assert (second.published, second.duplicate, second.invalid) == (0, 4, 1)
    assert len(read_topic(e2e, news_topic, 4, timeout_polls=6)) == 3
    assert len(list(e2e.store.list("raw/news/"))) == 3
    with e2e.sessions() as db:
        assert repo.run_progress(db, uuid.UUID(first.run_id)).get(S.LANDED.value) == 3


def test_mock_sec_document_to_minio(e2e):
    e2e.provider.on(SEC_PATH, ok(sec_body()))
    result = ingest(e2e, "company", request(symbols=("AAPL", "GOOGL")))
    assert (result.status, result.published) == (R.COMPLETED, 2)
    drain(e2e, 2)
    keys = [o.key for o in e2e.store.list("raw/company/")]
    assert len(keys) == 2
    alphabet = next(json.loads(e2e.store.get(k)) for k in keys if b"Alphabet" in e2e.store.get(k))
    assert alphabet["record"]["cik"] == "0001652044" and len(alphabet["original"]["data"]) == 4

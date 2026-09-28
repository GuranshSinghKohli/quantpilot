"""Research submission through a real broker. At-least-once, idempotent consumer.

SYNTHETIC data and FakeLLM only. Topics are the per-test prefix from conftest.
"""

from __future__ import annotations

import json
import time

from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.models import ResearchClaim, ResearchJob
from app.pipeline.kafka.config import producer_config
from app.pipeline.kafka.consumer import build_consumer
from app.pipeline.kafka.producer import EventProducer
from app.pipeline.retry import RetryPolicy
from app.research.llm import FakeLLMProvider
from app.research.outbox import publish_pending
from app.research.submit import submit_research
from app.research.worker import run_once, serve
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.package import ResearchRequest
from tests.unit.research.helpers import seed_partnership


def test_outbox_kafka_worker_is_idempotent(kafka_settings, topics, tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'research.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    world = seed_partnership(db)
    started = time.perf_counter()
    job = submit_research(
        db,
        ResearchRequest(query="AMD partnership with Meta", entity_ids=[world["amd"].entity_id]),
        owner_key="research-api",
        idempotency_key="kafka-1",
        correlation_id="corr-kafka",
        settings=kafka_settings,
    )
    db.commit()
    submit_seconds = time.perf_counter() - started
    research_id = job.id
    producer = EventProducer(
        producer_config(kafka_settings),
        policy=RetryPolicy(max_attempts=3, base_delay=0.05, max_delay=0.2, jitter=False),
    )
    try:
        publish_started = time.perf_counter()
        published = publish_pending(
            db,
            producer,
            worker_id="outbox-test",
            batch=10,
            lease_seconds=30,
            max_bytes=kafka_settings.KAFKA_MAX_MESSAGE_BYTES,
            policy=RetryPolicy(max_attempts=3, base_delay=0.05, max_delay=0.2, jitter=False),
        )
        db.commit()
        publish_seconds = time.perf_counter() - publish_started
        assert published == 1
        consumer = build_consumer(kafka_settings, kafka_settings.KAFKA_CONSUMER_GROUP_RESEARCH)
        consumer.subscribe([kafka_settings.KAFKA_TOPIC_RESEARCH_REQUEST])
        try:
            worker_started = time.perf_counter()
            handled = serve(
                Session,
                consumer,
                producer,
                kafka_settings,
                llm=FakeLLMProvider(),
                embed=FakeEmbeddingProvider(),
                worker_id="worker-test",
                max_messages=1,
                idle_timeout_s=30,
            )
        finally:
            consumer.close()
        assert handled == 1
        check = Session()
        try:
            stored = check.get(ResearchJob, research_id)
            assert stored.status == "succeeded"
            assert stored.correlation_id == "corr-kafka"
            queue_wait = None
            if stored.started_at and stored.queued_at:
                queue_wait = (stored.started_at - stored.queued_at).total_seconds()
            print(json.dumps({
                "fixture": "kafka_research_pipeline",
                "submit_seconds": round(submit_seconds, 4),
                "outbox_publish_seconds": round(publish_seconds, 4),
                "worker_seconds": round(time.perf_counter() - worker_started, 4),
                "queue_wait_seconds": None if queue_wait is None else round(queue_wait, 4),
                "note": "one FakeLLM fixture on local compose Kafka; not throughput or capacity",
            }))
            claims = check.query(ResearchClaim).filter_by(research_job_id=research_id).count()
            assert claims > 0
            body = check.query(ResearchJob).filter_by(id=research_id).one()
            event_row = body.outbox_events
            request_event = next(row.payload for row in event_row if row.kind == "request")
            run_once(
                check,
                json.dumps(request_event).encode(),
                kafka_settings,
                worker_id="worker-replay",
                llm=FakeLLMProvider(),
                embed=FakeEmbeddingProvider(),
            )
            check.commit()
            assert check.query(ResearchClaim).filter_by(research_job_id=research_id).count() == claims
            assert check.get(ResearchJob, research_id).status == "succeeded"
        finally:
            check.close()
    finally:
        producer.close()
        db.close()
        engine.dispose()

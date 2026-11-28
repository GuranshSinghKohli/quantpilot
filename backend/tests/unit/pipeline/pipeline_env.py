"""Shared wiring for publisher / lake-writer tests: a migrated tmp SQLite,
a tmp local lake, fake or real Kafka clients, and helpers. SYNTHETIC data only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.pipeline import synthetic
from app.pipeline.contracts import SOURCES
from app.pipeline.kafka.producer import DeadLetterPublisher, EventProducer
from app.pipeline.lake import LocalObjectStore
from app.pipeline.lake_writer import LakeWriter
from app.pipeline.metrics import Metrics
from app.pipeline.publisher import EventPublisher
from app.pipeline.retry import RetryPolicy
from app.pipeline.tracking import RunTracker

TOPICS = {"news": "test.u.news.raw", "market": "test.u.market.raw", "company": "test.u.company.raw"}
DLQ_TOPIC = "test.u.ingestion.dlq"
MAX_BYTES = 1_000_000
POLICY = RetryPolicy(max_attempts=3, base_delay=0.01, max_delay=0.02)


def migrated_sessions(path) -> sessionmaker:
    engine = build_engine(f"sqlite:///{path}")
    upgrade_to_head(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@dataclass
class Wiring:
    sessions: sessionmaker
    store: LocalObjectStore
    producer: EventProducer
    metrics: Metrics
    topics: Dict[str, str]
    dlq_topic: str
    sleeps: list

    def publisher(self) -> EventPublisher:
        return EventPublisher(session_factory=self.sessions, producer=self.producer,
                              dlq=DeadLetterPublisher(self.producer, self.dlq_topic), topics=self.topics,
                              max_bytes=MAX_BYTES)

    def writer(self, consumer, *, store=None, before_commit: Callable = None, policy=POLICY) -> LakeWriter:
        return LakeWriter(consumer=consumer, store=store or self.store, session_factory=self.sessions,
                          dlq=DeadLetterPublisher(self.producer, self.dlq_topic), topics=self.topics,
                          max_bytes=MAX_BYTES, policy=policy, metrics=self.metrics, poll_timeout=0.2,
                          sleep=self.sleeps.append, before_commit=before_commit)

    def start_run(self) -> RunTracker:
        tracker = RunTracker(self.sessions, SOURCES[synthetic.SOURCE], trigger="test")
        tracker.start()
        return tracker

    def publish_synthetic(self, count: int, *, ingested_at=None):
        tracker = self.start_run()
        events = synthetic.market_events(count, run_id=tracker.run_id, ingested_at=ingested_at)
        tracker.counters.received = len(events)
        summary = self.publisher().publish(tracker, events)
        return tracker, events, summary

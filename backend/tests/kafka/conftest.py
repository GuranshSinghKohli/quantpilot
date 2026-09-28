"""Kafka tier: the publisher and lake writer against a real broker.

Set TEST_KAFKA_BOOTSTRAP (e.g. localhost:9092, the compose broker). Without it
every test here is skipped. Each test gets its own topics (``test.<hex>.*``),
consumer group, tmp SQLite and tmp local lake; topics and groups are deleted
afterwards. Nothing touches the quantpilot.* topics or the developer database.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_KAFKA_BOOTSTRAP = os.getenv("TEST_KAFKA_BOOTSTRAP", "").strip()


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="TEST_KAFKA_BOOTSTRAP not set")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.kafka)
            if not TEST_KAFKA_BOOTSTRAP:
                item.add_marker(skip)


@pytest.fixture()
def kafka_settings():
    from app.core.settings import Settings

    prefix = f"test.{uuid.uuid4().hex[:12]}"
    return Settings(
        KAFKA_BOOTSTRAP_SERVERS=TEST_KAFKA_BOOTSTRAP,
        KAFKA_CLIENT_ID="quantpilot-test",
        KAFKA_TOPIC_NEWS_RAW=f"{prefix}.news.raw",
        KAFKA_TOPIC_MARKET_RAW=f"{prefix}.market.raw",
        KAFKA_TOPIC_COMPANY_RAW=f"{prefix}.company.raw",
        KAFKA_TOPIC_DLQ=f"{prefix}.ingestion.dlq",
        KAFKA_CONSUMER_GROUP_LAKE=f"{prefix}.lake-writer",
        KAFKA_TOPIC_RESEARCH_REQUEST=f"{prefix}.research.request",
        KAFKA_TOPIC_RESEARCH_COMPLETED=f"{prefix}.research.completed",
        KAFKA_TOPIC_RESEARCH_DLQ=f"{prefix}.research.dlq",
        KAFKA_CONSUMER_GROUP_RESEARCH=f"{prefix}.research-worker",
        KAFKA_DELIVERY_TIMEOUT_MS=5_000,
        KAFKA_POLL_TIMEOUT_SECONDS=0.2,
        PIPELINE_RETRY_MAX_ATTEMPTS=3,
        PIPELINE_RETRY_BASE_SECONDS=0.05,
        PIPELINE_RETRY_MAX_SECONDS=0.2,
    )


@pytest.fixture()
def admin(kafka_settings):
    from confluent_kafka.admin import AdminClient

    from app.pipeline.kafka.config import admin_config

    return AdminClient(admin_config(kafka_settings))


@pytest.fixture()
def topics(kafka_settings, admin):
    """Create this test's topics; delete them and its consumer groups afterwards."""
    from app.pipeline.kafka.topics import ensure_topics, topic_specs

    specs = topic_specs(kafka_settings)
    reports = ensure_topics(admin, specs)
    yield {"specs": specs, "reports": reports}
    names = [s.name for s in specs]
    assert all(n.startswith("test.") for n in names), names
    for future in admin.delete_topics(names, operation_timeout=30).values():
        future.result()
    stem = kafka_settings.KAFKA_TOPIC_NEWS_RAW.rsplit(".", 2)[0]
    groups = [g.group_id for g in admin.list_consumer_groups().result(timeout=10).valid
              if g.group_id.startswith(stem)]
    if groups:
        for future in admin.delete_consumer_groups(groups, request_timeout=10).values():
            future.result()

"""Cheap dependency probes for /ready and pipeline-status. Never used by /health."""

from __future__ import annotations

from typing import Any, Dict

from app.core.settings import Settings, get_settings


def probe_kafka(settings: Settings | None = None, timeout: float = 1.5) -> str:
    """ok | unavailable | unconfigured. A down broker must not raise."""
    settings = settings or get_settings()
    try:
        from confluent_kafka.admin import AdminClient

        from app.pipeline.kafka.config import admin_config

        admin = AdminClient(admin_config(settings))
        cluster = admin.list_topics(timeout=timeout)
        if cluster is None or not cluster.brokers:
            return "unavailable"
        return "ok"
    except Exception:
        return "unavailable"


def probe_lake(settings: Settings | None = None) -> str:
    """ok | unavailable. Uses ObjectStore.check only — no write-once probe."""
    settings = settings or get_settings()
    try:
        from app.pipeline.lake import build_object_store

        report = build_object_store(settings).check()
        if settings.LAKE_BACKEND == "s3":
            return "ok" if report.get("versioning") == "Enabled" else "unavailable"
        return "ok" if report.get("exists") and report.get("writable") else "unavailable"
    except Exception:
        return "unavailable"


def kafka_lag(settings: Settings | None = None, timeout: float = 2.0) -> Dict[str, Any]:
    """Committed lag for the lake-writer group. Empty dict if the broker is down."""
    settings = settings or get_settings()
    try:
        from confluent_kafka import Consumer, TopicPartition

        from app.pipeline.kafka.config import consumer_config
        from app.pipeline.kafka.consumer import committed_lag
        from app.pipeline.kafka.topics import topic_specs

        specs = [s for s in topic_specs(settings) if s.name != settings.KAFKA_TOPIC_DLQ]
        probe = Consumer(consumer_config(settings, settings.KAFKA_CONSUMER_GROUP_LAKE))
        try:
            partitions = [TopicPartition(s.name, p) for s in specs for p in range(s.partitions)]
            lag = committed_lag(probe, partitions)
        finally:
            probe.close()
        return {f"{topic}[{part}]": value for (topic, part), value in sorted(lag.items())}
    except Exception:
        return {}

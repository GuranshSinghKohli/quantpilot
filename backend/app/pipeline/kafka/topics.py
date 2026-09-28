"""The topic set, created from ``Settings`` and verified on every ``kafka init``.

Existing topics are never altered: a partition, replication or retention
mismatch is reported as drift and fails the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.pipeline.kafka.config import DLQ_SIZE_FACTOR, RAW_OVERHEAD_BYTES

_PARTITIONS = {"news": 3, "market": 3, "company": 1}
_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class TopicSpec:
    name: str
    purpose: str
    partitions: int
    replication_factor: int
    retention_ms: int
    max_message_bytes: int

    def config(self) -> Dict[str, str]:
        return {
            "cleanup.policy": "delete",
            "retention.ms": str(self.retention_ms),
            "max.message.bytes": str(self.max_message_bytes),
            "min.insync.replicas": str(max(1, self.replication_factor - 1)),
        }


def topic_specs(settings) -> List[TopicSpec]:
    rf = settings.KAFKA_REPLICATION_FACTOR
    raw_ms = settings.KAFKA_RAW_RETENTION_HOURS * _HOUR_MS
    specs = [
        TopicSpec(topic, f"raw {domain} ingestion events", _PARTITIONS[domain], rf, raw_ms,
                  settings.KAFKA_MAX_MESSAGE_BYTES + RAW_OVERHEAD_BYTES)
        for domain, topic in settings.raw_topics.items()
    ]
    specs.append(
        TopicSpec(settings.KAFKA_TOPIC_DLQ, "dead letters from any pipeline component", 1, rf,
                  settings.KAFKA_DLQ_RETENTION_HOURS * _HOUR_MS,
                  settings.KAFKA_MAX_MESSAGE_BYTES * DLQ_SIZE_FACTOR + RAW_OVERHEAD_BYTES)
    )
    research_ms = settings.KAFKA_RESEARCH_RETENTION_HOURS * _HOUR_MS
    research_partitions = settings.KAFKA_RESEARCH_PARTITIONS
    specs.extend(
        [
            TopicSpec(
                settings.KAFKA_TOPIC_RESEARCH_REQUEST,
                "durable research execution requests",
                research_partitions,
                rf,
                research_ms,
                settings.KAFKA_MAX_MESSAGE_BYTES,
            ),
            TopicSpec(
                settings.KAFKA_TOPIC_RESEARCH_COMPLETED,
                "bounded research completion notices",
                research_partitions,
                rf,
                research_ms,
                settings.KAFKA_MAX_MESSAGE_BYTES,
            ),
            TopicSpec(
                settings.KAFKA_TOPIC_RESEARCH_DLQ,
                "research messages that cannot be processed",
                1,
                rf,
                settings.KAFKA_DLQ_RETENTION_HOURS * _HOUR_MS,
                settings.KAFKA_MAX_MESSAGE_BYTES * DLQ_SIZE_FACTOR + RAW_OVERHEAD_BYTES,
            ),
        ]
    )
    return specs


def ensure_topics(admin, specs: List[TopicSpec], *, timeout: float = 30.0) -> List[Dict[str, Any]]:
    """Create missing topics; verify existing ones. Returns one report per topic."""
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import NewTopic

    existing = admin.list_topics(timeout=timeout).topics
    missing = [s for s in specs if s.name not in existing]
    created = set()
    if missing:
        futures = admin.create_topics(
            [NewTopic(s.name, num_partitions=s.partitions, replication_factor=s.replication_factor, config=s.config())
             for s in missing],
            operation_timeout=timeout,
            request_timeout=timeout,
        )
        for name, future in futures.items():
            try:
                future.result()
                created.add(name)
            except KafkaException as exc:
                if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                    raise
    return [
        {"topic": s.name, "action": "created", "problems": []} if s.name in created
        else {"topic": s.name, "action": "verified", "problems": verify_topic(admin, s, timeout=timeout)}
        for s in specs
    ]


def verify_topic(admin, spec: TopicSpec, *, timeout: float = 30.0) -> List[str]:
    from confluent_kafka.admin import ConfigResource

    metadata = admin.list_topics(topic=spec.name, timeout=timeout).topics.get(spec.name)
    if metadata is None or metadata.error is not None:
        return [f"topic missing or unreadable: {getattr(metadata, 'error', None)}"]
    problems = []
    partitions = metadata.partitions
    if len(partitions) != spec.partitions:
        problems.append(f"partitions {len(partitions)} != expected {spec.partitions}")
    replicas = {len(p.replicas) for p in partitions.values()}
    if replicas != {spec.replication_factor}:
        problems.append(f"replication factor {sorted(replicas)} != expected {spec.replication_factor}")
    resource = ConfigResource(ConfigResource.Type.TOPIC, spec.name)
    configs = admin.describe_configs([resource], request_timeout=timeout)[resource].result()
    for key in ("retention.ms", "cleanup.policy"):
        actual = configs[key].value if key in configs else None
        if actual != spec.config()[key]:
            problems.append(f"{key} {actual} != expected {spec.config()[key]}")
    return problems

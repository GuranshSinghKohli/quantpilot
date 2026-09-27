"""librdkafka client configuration derived from ``Settings`` (the only source)."""

from __future__ import annotations

from typing import Any, Dict

# Room for the DLQ envelope around a payload, and base64 growth (4/3) of a
# non-UTF-8 original. Raw events themselves are capped at KAFKA_MAX_MESSAGE_BYTES.
DLQ_SIZE_FACTOR = 2
RAW_OVERHEAD_BYTES = 64 * 1024


def client_config(settings) -> Dict[str, Any]:
    conf: Dict[str, Any] = {
        "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
        "client.id": settings.KAFKA_CLIENT_ID,
        "security.protocol": settings.KAFKA_SECURITY_PROTOCOL,
    }
    if settings.KAFKA_SECURITY_PROTOCOL.startswith("SASL_"):
        conf.update(
            {
                "sasl.mechanism": settings.KAFKA_SASL_MECHANISM,
                "sasl.username": settings.KAFKA_SASL_USERNAME,
                "sasl.password": settings.KAFKA_SASL_PASSWORD.get_secret_value(),
            }
        )
    return conf


def producer_config(settings, **overrides: Any) -> Dict[str, Any]:
    conf = client_config(settings)
    conf.update(
        {
            # Idempotent producer: acks=all, ordered retries inside one session,
            # no duplicates from librdkafka's own resends.
            "enable.idempotence": True,
            "acks": "all",
            "delivery.timeout.ms": settings.KAFKA_DELIVERY_TIMEOUT_MS,
            "message.max.bytes": settings.KAFKA_MAX_MESSAGE_BYTES * DLQ_SIZE_FACTOR,
            "linger.ms": 5,
        }
    )
    conf.update(overrides)
    return conf


def consumer_config(settings, group_id: str, **overrides: Any) -> Dict[str, Any]:
    conf = client_config(settings)
    conf.update(
        {
            "group.id": group_id,
            # Offsets are committed explicitly, only after durable processing.
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "earliest",
            "session.timeout.ms": 45_000,
            "max.poll.interval.ms": 300_000,
            "fetch.max.bytes": settings.KAFKA_MAX_MESSAGE_BYTES * DLQ_SIZE_FACTOR + RAW_OVERHEAD_BYTES,
        }
    )
    conf.update(overrides)
    return conf


def admin_config(settings) -> Dict[str, Any]:
    return client_config(settings)

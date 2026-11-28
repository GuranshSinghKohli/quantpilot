"""Manual-commit consumer construction and lag measurement."""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Tuple

from app.pipeline.kafka.config import consumer_config
from app.pipeline.retry import RetryPolicy, call_with_retry

OFFSET_UNSET = -1001  # librdkafka OFFSET_INVALID: nothing committed yet
# The group coordinator can be briefly unavailable (e.g. while the broker creates
# __consumer_offsets on first use); these lookups are read-only, so retry them.
_LOOKUP_POLICY = RetryPolicy(max_attempts=6, base_delay=0.5, max_delay=4.0)


def build_consumer(settings, group_id: str, **overrides):
    from confluent_kafka import Consumer

    return Consumer(consumer_config(settings, group_id, **overrides))


def _retriable_kafka(error: BaseException) -> bool:
    from confluent_kafka import KafkaException

    return isinstance(error, KafkaException) and bool(error.args) and error.args[0].retriable()


def committed_lag(
    consumer, partitions: List, *, timeout: float = 5.0, sleep: Callable[[float], None] = time.sleep
) -> Dict[Tuple[str, int], int]:
    """Per partition: high watermark minus the group's committed offset
    (minus the low watermark when nothing is committed yet)."""
    if not partitions:
        return {}
    committed = call_with_retry(lambda: consumer.committed(partitions, timeout=timeout),
                                operation="kafka.committed", policy=_LOOKUP_POLICY,
                                classify=_retriable_kafka, sleep=sleep)
    lag = {}
    for tp in committed:
        low, high = consumer.get_watermark_offsets(tp, timeout=timeout, cached=False)
        position = tp.offset if tp.offset >= 0 else low
        lag[(tp.topic, tp.partition)] = max(high - position, 0)
    return lag

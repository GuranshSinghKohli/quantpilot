"""Read-only DLQ inspection. Never commits offsets. Never replays."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.core.settings import get_settings
from app.pipeline.serde import decode_dead_letter


def inspect_dlq(*, limit: int = 50, show_payload: bool = False, timeout: float = 1.0) -> Dict[str, Any]:
    settings = get_settings()
    topic = settings.KAFKA_TOPIC_DLQ
    try:
        from confluent_kafka import OFFSET_BEGINNING, Consumer, TopicPartition

        from app.pipeline.kafka.config import consumer_config
    except Exception as exc:
        return {"topic": topic, "available": False, "error": type(exc).__name__, "count": 0, "dead_letters": []}

    consumer = Consumer(consumer_config(settings, f"quantpilot.dlq-reader.{uuid.uuid4().hex[:8]}"))
    letters: List[Dict[str, Any]] = []
    try:
        meta = consumer.list_topics(topic, timeout=10)
        partitions = meta.topics[topic].partitions
        consumer.assign([TopicPartition(topic, p, OFFSET_BEGINNING) for p in partitions])
        empty = 0
        while len(letters) < limit and empty < 3:
            message = consumer.poll(timeout)
            if message is None:
                empty += 1
                continue
            if message.error():
                continue
            letter = decode_dead_letter(message.value())
            item = letter.model_dump(mode="json", exclude=set() if show_payload else {"original_value"})
            item["dlq_offset"] = message.offset()
            letters.append(item)
    except Exception as exc:
        return {"topic": topic, "available": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                "count": 0, "dead_letters": []}
    finally:
        consumer.close()
    return {"topic": topic, "available": True, "count": len(letters), "dead_letters": letters,
            "replay": "not implemented; do not replay automatically"}


def dlq_summary(*, limit: int = 500) -> Dict[str, Any]:
    raw = inspect_dlq(limit=limit, show_payload=False)
    if not raw.get("available"):
        return {"available": False, "topic": raw.get("topic"), "count": 0, "error": raw.get("error")}
    by_component: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    first: Optional[str] = None
    last: Optional[str] = None
    retries = 0
    for item in raw["dead_letters"]:
        by_component[item.get("component") or "unknown"] = by_component.get(item.get("component") or "unknown", 0) + 1
        by_reason[item.get("failure_reason") or "unknown"] = by_reason.get(item.get("failure_reason") or "unknown", 0) + 1
        failed = item.get("failed_at") or item.get("first_failed_at")
        if failed:
            first = failed if first is None or failed < first else first
            last = failed if last is None or failed > last else last
        retries += int(item.get("retry_count") or 0)
    return {
        "available": True,
        "topic": raw["topic"],
        "count": raw["count"],
        "sampled": len(raw["dead_letters"]),
        "by_component": by_component,
        "by_reason": by_reason,
        "first_failed_at": first,
        "last_failed_at": last,
        "retry_count_sum": retries,
        "replay": "not implemented; do not replay automatically",
    }

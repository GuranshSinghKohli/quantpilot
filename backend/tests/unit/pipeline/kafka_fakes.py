"""Stand-ins for confluent-kafka's Producer, Consumer and Message.

They mimic only what the pipeline uses: produce/poll/flush with delivery
callbacks, and poll/commit/subscribe on the consumer. Errors are real
``confluent_kafka.KafkaError`` instances so retry classification is exercised.
"""

from __future__ import annotations

import zlib
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from confluent_kafka import KafkaError


class FakeMessage:
    def __init__(self, topic, partition, offset, key, value, headers=None, error=None):
        self._t, self._p, self._o, self._k, self._v = topic, partition, offset, key, value
        self._h, self._e = headers or [], error

    def topic(self): return self._t
    def partition(self): return self._p
    def offset(self): return self._o
    def key(self): return self._k
    def value(self): return self._v
    def headers(self): return self._h
    def error(self): return self._e


class FakeProducer:
    """``behaviour(topic, key, attempt)`` may return a KafkaError (failed delivery
    report), "no-report" (callback never fires) or None (acknowledged)."""

    def __init__(self, behaviour: Optional[Callable] = None, partitions: int = 3):
        self.behaviour = behaviour or (lambda topic, key, attempt: None)
        self.partitions = partitions
        self.produced: List[FakeMessage] = []  # acknowledged messages, in order
        self.attempts: Dict[bytes, int] = defaultdict(int)
        self._offsets: Dict[Tuple[str, int], int] = defaultdict(int)
        self._pending: list = []

    def produce(self, topic, key=None, value=None, headers=None, on_delivery=None):
        self.attempts[key] += 1
        self._pending.append((topic, key, value, headers, on_delivery, self.attempts[key]))

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        pending, self._pending = self._pending, []
        for topic, key, value, headers, callback, attempt in pending:
            outcome = self.behaviour(topic, key, attempt)
            if isinstance(outcome, str):  # "no-report"
                continue
            if isinstance(outcome, KafkaError):
                callback(outcome, FakeMessage(topic, -1, -1, key, value, headers))
                continue
            partition = zlib.crc32(key or b"") % self.partitions
            offset = self._offsets[(topic, partition)]
            self._offsets[(topic, partition)] += 1
            message = FakeMessage(topic, partition, offset, key, value, headers)
            self.produced.append(message)
            callback(None, message)
        return 0

    def on(self, topic: str) -> List[FakeMessage]:
        return [m for m in self.produced if m.topic() == topic]


class FakeConsumer:
    def __init__(self, messages: Optional[List[FakeMessage]] = None):
        self.queue: List[FakeMessage] = list(messages or [])
        self.committed_offsets: Dict[Tuple[str, int], int] = {}
        self.subscribed: List[str] = []

    def subscribe(self, topics):
        self.subscribed = list(topics)

    def poll(self, timeout=None):
        return self.queue.pop(0) if self.queue else None

    def commit(self, message=None, asynchronous=True):
        self.committed_offsets[(message.topic(), message.partition())] = message.offset() + 1

    def assignment(self):
        return []

    def committed(self, partitions, timeout=None):
        return partitions

    def close(self):
        pass

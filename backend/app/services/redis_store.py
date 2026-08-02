"""Redis client with in-memory fallback for local/dev without Redis."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import QUOTE_CACHE_TTL_SECONDS, REDIS_URL
from app.observability.logger import get_logger, log_event

logger = get_logger("redis")

_ALERT_QUEUE_KEY = "quantpilot:queue:alert_eval"
INVESTIGATION_QUEUE_KEY = "quantpilot:queue:investigation_jobs"
_memory_lock = threading.Lock()
_memory_cache: Dict[str, Any] = {}
_memory_expiry: Dict[str, float] = {}
_memory_queues: Dict[str, List[str]] = {}
_redis_client = None
_redis_mode: Optional[str] = None  # "redis" | "memory"


def _connect():
    global _redis_client, _redis_mode
    if _redis_mode is not None:
        return _redis_client

    if not REDIS_URL:
        _redis_mode = "memory"
        log_event(logger, logging.INFO, "Redis disabled; using in-memory cache/queue")
        return None

    try:
        import redis

        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _redis_client = client
        _redis_mode = "redis"
        log_event(logger, logging.INFO, "Redis connected", url_set=True)
        return client
    except Exception as exc:
        _redis_mode = "memory"
        _redis_client = None
        log_event(
            logger,
            logging.WARNING,
            "Redis unavailable; falling back to in-memory cache/queue",
            error=str(exc),
        )
        return None


def redis_mode() -> str:
    _connect()
    return _redis_mode or "memory"


def cache_get(key: str) -> Optional[Any]:
    client = _connect()
    if client is not None:
        raw = client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    with _memory_lock:
        exp = _memory_expiry.get(key)
        if exp is not None and exp < time.time():
            _memory_cache.pop(key, None)
            _memory_expiry.pop(key, None)
            return None
        return _memory_cache.get(key)


def cache_set(key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
    ttl = ttl_seconds if ttl_seconds is not None else QUOTE_CACHE_TTL_SECONDS
    client = _connect()
    payload = json.dumps(value, default=str)
    if client is not None:
        client.setex(key, max(1, ttl), payload)
        return

    with _memory_lock:
        try:
            _memory_cache[key] = json.loads(payload)
        except json.JSONDecodeError:
            _memory_cache[key] = value
        _memory_expiry[key] = time.time() + max(1, ttl)


def queue_push(payload: Dict[str, Any], queue_key: str = _ALERT_QUEUE_KEY) -> None:
    client = _connect()
    raw = json.dumps(payload, default=str)
    if client is not None:
        client.rpush(queue_key, raw)
        return
    with _memory_lock:
        _memory_queues.setdefault(queue_key, []).append(raw)


def queue_pop(queue_key: str = _ALERT_QUEUE_KEY) -> Optional[Dict[str, Any]]:
    client = _connect()
    raw = None
    if client is not None:
        raw = client.lpop(queue_key)
    else:
        with _memory_lock:
            q = _memory_queues.get(queue_key) or []
            raw = q.pop(0) if q else None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def quote_cache_key(ticker: str) -> str:
    return f"quantpilot:quote:{ticker.upper().strip()}"

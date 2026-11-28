"""Lightweight in-process rate limiter for expensive endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from app import config as app_config


_lock = threading.Lock()
_hits: Dict[str, Deque[float]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def check_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int = 0,
    window_seconds: int = 60,
) -> None:
    if not app_config.RATE_LIMIT_ENABLED:
        return
    cap = limit or app_config.RATE_LIMIT_EXPENSIVE_PER_MINUTE
    if cap <= 0:
        return
    key = f"{_client_key(request)}:{bucket}"
    now = time.time()
    with _lock:
        q = _hits[key]
        cutoff = now - window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="RATE_LIMITED",
            )
        q.append(now)


async def expensive_limit(request: Request) -> None:
    check_rate_limit(request, bucket=request.url.path)

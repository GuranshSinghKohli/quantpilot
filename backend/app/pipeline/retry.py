"""Bounded retries with exponential backoff and full jitter.

Only transient failures are retried. Permanent failures propagate on the first
attempt. After ``max_attempts`` the last error is wrapped in ``RetryExhausted``
so callers can dead-letter it with the retry count.
"""

from __future__ import annotations

import logging
import random
import socket
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Type, TypeVar

from app.core.structured_logging import get_logger, log_event
from app.pipeline.errors import PermanentError, RetryExhausted, TransientError

T = TypeVar("T")

_TRANSIENT_TYPES: Tuple[Type[BaseException], ...] = (
    TransientError,
    TimeoutError,
    ConnectionError,
    socket.timeout,
)
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

_log = get_logger("quantpilot.pipeline.retry")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("need 0 < base_delay <= max_delay")

    @classmethod
    def from_settings(cls, settings) -> "RetryPolicy":
        return cls(
            max_attempts=settings.PIPELINE_RETRY_MAX_ATTEMPTS,
            base_delay=settings.PIPELINE_RETRY_BASE_SECONDS,
            max_delay=settings.PIPELINE_RETRY_MAX_SECONDS,
        )

    def ceiling(self, attempt: int) -> float:
        """Upper bound of the wait after failed attempt ``attempt`` (1-based)."""
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))

    def delay(self, attempt: int, rand: Callable[[], float] = random.random) -> float:
        ceiling = self.ceiling(attempt)
        return rand() * ceiling if self.jitter else ceiling


def is_transient(error: BaseException) -> bool:
    if isinstance(error, PermanentError):
        return False
    if isinstance(error, _TRANSIENT_TYPES):
        return True
    status = _status_code(error)
    if status is not None:
        return status in _TRANSIENT_STATUS
    # Driver/transport errors by name, without importing optional libraries here.
    return type(error).__name__ in {
        "OperationalError",  # SQLAlchemy / DB-API: connection lost, lock timeout
        "TransportError",  # httpx base for connect/read/timeout errors
        "ConnectError",
        "ReadTimeout",
        "EndpointConnectionError",  # botocore
        "ConnectTimeoutError",
        "ReadTimeoutError",
    }


def _status_code(error: BaseException) -> Optional[int]:
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    # botocore ClientError: response is a dict
    if isinstance(response, dict):
        meta = response.get("ResponseMetadata") or {}
        code = meta.get("HTTPStatusCode")
        if isinstance(code, int):
            return code
    return None


def call_with_retry(
    fn: Callable[[], T],
    *,
    operation: str,
    policy: RetryPolicy,
    classify: Callable[[BaseException], bool] = is_transient,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    logger: logging.Logger = _log,
) -> T:
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not classify(exc):
                raise
            if attempt == policy.max_attempts:
                log_event(
                    logger,
                    logging.ERROR,
                    "pipeline.retry.exhausted",
                    operation=operation,
                    attempts=attempt,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )
                raise RetryExhausted(operation, attempt, exc) from exc
            wait = policy.delay(attempt, rand)
            log_event(
                logger,
                logging.WARNING,
                "pipeline.retry",
                operation=operation,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                delay_seconds=round(wait, 3),
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            sleep(wait)
    raise AssertionError("unreachable")

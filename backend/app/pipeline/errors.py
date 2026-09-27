"""Failure taxonomy. Permanent failures go to the DLQ at once; transient ones are
retried with backoff and only dead-lettered after the retry budget is spent."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Dict, List, Optional


class FailureReason(StrEnum):
    DESERIALIZE = "deserialize"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    ENVELOPE_INVALID = "envelope_invalid"
    PAYLOAD_INVALID = "payload_invalid"
    HASH_MISMATCH = "hash_mismatch"
    OVERSIZE = "oversize"
    RETRIES_EXHAUSTED = "retries_exhausted"


class PipelineError(Exception):
    pass


class TransientError(PipelineError):
    """Worth retrying: the same input may succeed later (timeouts, throttling, 5xx)."""


class PermanentError(PipelineError):
    """Retrying the same input cannot succeed. Carries a DLQ reason and details."""

    def __init__(
        self,
        reason: FailureReason,
        message: str,
        details: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or []


class RetryExhausted(PipelineError):
    def __init__(self, operation: str, attempts: int, last_error: BaseException) -> None:
        super().__init__(
            f"{operation} failed after {attempts} attempts: "
            f"{type(last_error).__name__}: {last_error}"
        )
        self.operation = operation
        self.attempts = attempts
        self.last_error = last_error

    @property
    def retry_count(self) -> int:
        return self.attempts - 1

"""HTTP access to external providers: throttle → request → classify → bounded retry.

Every provider request goes through ``SourceClient.get_json`` so that rate limits,
retries and failure kinds behave the same for all adapters. Credentials live in
the client's default headers only: they never appear in URLs, logs or errors.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from app.core.structured_logging import get_logger, log_event
from app.pipeline.errors import PipelineError, TransientError
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy, call_with_retry

_log = get_logger("quantpilot.pipeline.sources.http")

_RATE_LIMIT_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after")


class SourceFailure(StrEnum):
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"  # timeout, connection refused/reset
    OUTAGE = "outage"  # 408, 5xx
    AUTH = "auth"  # 401, 403
    INVALID_REQUEST = "invalid_request"  # 400, 404, 422, other 4xx
    BAD_RESPONSE = "bad_response"  # 2xx that is not the documented shape


class SourceRequestError(PipelineError):
    """A provider request failed. ``fatal_for_run``: other streams of the same run
    would fail the same way (bad credentials, a long rate-limit window)."""

    retryable = False

    def __init__(
        self,
        kind: SourceFailure,
        message: str,
        *,
        provider: str,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
        fatal_for_run: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.status = status
        self.retry_after = retry_after
        self.fatal_for_run = fatal_for_run


class TransientSourceError(SourceRequestError, TransientError):
    retryable = True


class PermanentSourceError(SourceRequestError):
    """Never retried."""


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Retry-After is either delta-seconds or an HTTP date. Unparseable → None."""
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


class Throttle:
    """Minimum spacing between requests (client-side rate limit)."""

    def __init__(
        self,
        requests_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")
        self.interval = 60.0 / requests_per_minute
        self._clock, self._sleep = clock, sleep
        self._last: Optional[float] = None

    def wait(self) -> float:
        waited = 0.0
        if self._last is not None:
            remaining = self.interval - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
                waited = remaining
        self._last = self._clock()
        return waited


def _provider_message(response: httpx.Response) -> str:
    """The provider's own error text (Massive: ``error``/``message``), bounded."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or ""
    if isinstance(body, dict):
        for key in ("error", "message", "status"):
            if isinstance(body.get(key), str):
                return body[key][:300]
    return response.reason_phrase or ""


class SourceClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        headers: Optional[Mapping[str, str]] = None,
        timeout_s: float,
        throttle: Throttle,
        policy: RetryPolicy,
        max_retry_after_s: float,
        metrics: Optional[Metrics] = None,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).netloc
        self.throttle = throttle
        self.policy = policy
        self.max_retry_after_s = max_retry_after_s
        self.metrics = metrics or Metrics()
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=self.base_url,
            headers=dict(headers or {}),
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def get_json(
        self, target: str, params: Optional[Dict[str, Any]] = None, *, operation: str, retry: bool = True
    ) -> Tuple[Any, httpx.Headers]:
        """GET ``target`` (a path, or an absolute URL on the provider's own host)."""
        url = self._checked_target(target)
        policy = self.policy if retry else RetryPolicy(max_attempts=1, base_delay=self.policy.base_delay,
                                                       max_delay=self.policy.max_delay)
        return call_with_retry(
            lambda: self._once("GET", url, params, operation, parse=True),
            operation=f"{self.provider}.{operation}",
            policy=policy,
            sleep=self._sleep,
        )

    def probe(self, method: str, target: str, params: Optional[Dict[str, Any]] = None, *, operation: str):
        """One unretried request for health checks: returns (status, headers, latency_s)."""
        url = self._checked_target(target)
        started = time.monotonic()
        self.throttle.wait()
        response = self._http.request(method, url, params=params)
        latency = time.monotonic() - started
        self._record(operation, response.status_code, latency, response.headers)
        return response.status_code, response.headers, latency

    def _checked_target(self, target: str) -> str:
        if target.startswith(("http://", "https://")):
            if urlsplit(target).netloc != self._host:
                # Pagination links are provider-controlled; never send the key elsewhere.
                raise PermanentSourceError(
                    SourceFailure.BAD_RESPONSE,
                    f"refusing to follow a link to {urlsplit(target).netloc!r} (expected {self._host!r})",
                    provider=self.provider,
                )
            return target
        return target if target.startswith("/") else f"/{target}"

    def _once(self, method: str, url: str, params, operation: str, *, parse: bool):
        waited = self.throttle.wait()
        if waited:
            self.metrics.inc("source_throttle_wait_seconds_total", waited, provider=self.provider)
        started = time.monotonic()
        try:
            response = self._http.request(method, url, params=params)
        except httpx.TimeoutException as exc:
            self._record(operation, "timeout", time.monotonic() - started, None)
            raise TransientSourceError(SourceFailure.NETWORK, f"timeout: {type(exc).__name__}",
                                       provider=self.provider) from exc
        except httpx.TransportError as exc:
            self._record(operation, "network_error", time.monotonic() - started, None)
            raise TransientSourceError(SourceFailure.NETWORK, f"{type(exc).__name__}: {exc}"[:300],
                                       provider=self.provider) from exc
        self._record(operation, response.status_code, time.monotonic() - started, response.headers)
        self._raise_for_status(response)
        if not parse:
            return None, response.headers
        try:
            return response.json(), response.headers
        except ValueError as exc:
            raise PermanentSourceError(SourceFailure.BAD_RESPONSE, "response body is not JSON",
                                       provider=self.provider, status=response.status_code) from exc

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        detail = f"HTTP {status}: {_provider_message(response)}".strip()
        kwargs = dict(provider=self.provider, status=status)
        if status == 429:
            self.metrics.inc("source_rate_limited_total", provider=self.provider)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            if retry_after is not None and retry_after > self.max_retry_after_s:
                raise PermanentSourceError(
                    SourceFailure.RATE_LIMITED,
                    f"{detail}; Retry-After {retry_after:.0f}s exceeds SOURCE_MAX_RETRY_AFTER_SECONDS "
                    f"({self.max_retry_after_s:.0f}s)",
                    retry_after=retry_after, fatal_for_run=True, **kwargs,
                )
            raise TransientSourceError(SourceFailure.RATE_LIMITED, detail, retry_after=retry_after, **kwargs)
        if status == 408 or status >= 500:
            raise TransientSourceError(SourceFailure.OUTAGE, detail, **kwargs)
        if status in (401, 403):
            raise PermanentSourceError(SourceFailure.AUTH, detail, fatal_for_run=True, **kwargs)
        if 400 <= status < 500:
            raise PermanentSourceError(SourceFailure.INVALID_REQUEST, detail, **kwargs)
        raise PermanentSourceError(SourceFailure.BAD_RESPONSE, f"unexpected {detail}", **kwargs)

    def _record(self, operation: str, status, latency: float, headers: Optional[httpx.Headers]) -> None:
        self.metrics.inc("source_requests_total", provider=self.provider, status=status)
        self.metrics.observe("source_request_latency_seconds", latency, provider=self.provider)
        log_event(
            _log,
            logging.INFO,
            "pipeline.source.request",
            provider=self.provider,
            operation=operation,
            status=status,
            latency_ms=round(latency * 1000, 1),
            rate_limit=rate_limit_headers(headers),
        )


def rate_limit_headers(headers: Optional[httpx.Headers]) -> Optional[Dict[str, str]]:
    if headers is None:
        return None
    found = {name: headers[name] for name in _RATE_LIMIT_HEADERS if name in headers}
    return found or None

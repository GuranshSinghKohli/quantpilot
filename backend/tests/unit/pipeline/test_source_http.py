"""SourceClient: throttling, status classification, bounded retries, Retry-After,
and credential hygiene. Mock transport only."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.pipeline.errors import RetryExhausted
from app.pipeline.metrics import Metrics
from app.pipeline.retry import RetryPolicy
from app.pipeline.sources.http import (
    PermanentSourceError,
    SourceClient,
    SourceFailure,
    Throttle,
    TransientSourceError,
    parse_retry_after,
)
from tests.unit.pipeline.source_harness import BASE, TEST_KEY, FakeClock, MockProvider, ok, status


def client(provider: MockProvider, clock: FakeClock, *, per_minute=6000, attempts=3, max_retry_after=30):
    return SourceClient(
        provider="mock", base_url=BASE, headers={"Authorization": f"Bearer {TEST_KEY}"}, timeout_s=5,
        throttle=Throttle(per_minute, clock=clock, sleep=clock.sleep),
        policy=RetryPolicy(max_attempts=attempts, base_delay=0.5, max_delay=2, jitter=False),
        max_retry_after_s=max_retry_after, metrics=Metrics(), transport=provider.transport(), sleep=clock.sleep,
    )


@pytest.fixture()
def mock():
    return MockProvider(), FakeClock()


def test_success_returns_json_and_sends_key_only_in_header(mock):
    provider, clock = mock
    provider.on("/x", ok({"results": []}))
    body, _ = client(provider, clock).get_json("/x", {"a": "1"}, operation="x")
    assert body == {"results": []}
    sent = provider.requests[0]
    assert sent.headers["authorization"] == f"Bearer {TEST_KEY}"
    assert TEST_KEY not in str(sent.url)


@pytest.mark.parametrize("code,kind", [(400, SourceFailure.INVALID_REQUEST), (404, SourceFailure.INVALID_REQUEST),
                                       (422, SourceFailure.INVALID_REQUEST), (401, SourceFailure.AUTH),
                                       (403, SourceFailure.AUTH)])
def test_permanent_statuses_are_sent_once(mock, code, kind):
    provider, clock = mock
    provider.on("/x", status(code))
    with pytest.raises(PermanentSourceError) as err:
        client(provider, clock).get_json("/x", operation="x")
    assert err.value.kind == kind and err.value.status == code
    assert err.value.fatal_for_run is (kind == SourceFailure.AUTH)
    assert provider.hits() == 1


@pytest.mark.parametrize("failure", [status(500), status(502), status(503), status(408),
                                     httpx.ReadTimeout("simulated timeout"),
                                     httpx.ConnectError("simulated connection refused")])
def test_transient_failures_are_retried_then_exhausted(mock, failure):
    provider, clock = mock
    provider.on("/x", failure)
    with pytest.raises(RetryExhausted) as err:
        client(provider, clock, attempts=3).get_json("/x", operation="x")
    assert provider.hits() == 3
    assert isinstance(err.value.last_error, TransientSourceError)
    assert [s for s in clock.sleeps if s >= 0.5] == [0.5, 1.0]  # exponential backoff, no jitter


def test_transient_failure_then_success(mock):
    provider, clock = mock
    provider.on("/x", status(503), httpx.ReadTimeout("simulated"), ok({"ok": True}))
    body, _ = client(provider, clock).get_json("/x", operation="x")
    assert body == {"ok": True} and provider.hits() == 3


def test_429_waits_for_retry_after_seconds(mock):
    provider, clock = mock
    provider.on("/x", status(429, headers={"Retry-After": "7"}), ok({"ok": True}))
    client(provider, clock).get_json("/x", operation="x")
    assert 7.0 in clock.sleeps  # the server's wait beats the 0.5 s backoff


def test_429_retry_after_http_date():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert parse_retry_after(format_datetime(now + timedelta(seconds=12), usegmt=True), now) == 12
    assert parse_retry_after("soon") is None and parse_retry_after(None) is None


def test_429_beyond_budget_is_not_retried_and_stops_the_run(mock):
    provider, clock = mock
    provider.on("/x", status(429, headers={"Retry-After": "3600"}))
    with pytest.raises(PermanentSourceError) as err:
        client(provider, clock, max_retry_after=30).get_json("/x", operation="x")
    assert err.value.kind == SourceFailure.RATE_LIMITED and err.value.fatal_for_run
    assert provider.hits() == 1 and 3600 not in clock.sleeps


def test_429_without_retry_after_uses_backoff_and_is_bounded(mock):
    provider, clock = mock
    provider.on("/x", status(429))
    with pytest.raises(RetryExhausted) as err:
        client(provider, clock, attempts=2).get_json("/x", operation="x")
    assert err.value.last_error.kind == SourceFailure.RATE_LIMITED and provider.hits() == 2


def test_throttle_spaces_requests(mock):
    provider, clock = mock
    provider.on("/x", ok({}))
    c = client(provider, clock, per_minute=5)
    for _ in range(3):
        c.get_json("/x", operation="x")
    assert clock.sleeps == [12.0, 12.0]  # 5/min → 12 s apart; first request immediate


@pytest.mark.parametrize("body", [b"<html>oops</html>", b""])
def test_non_json_success_is_a_bad_response(mock, body):
    provider, clock = mock
    provider.on("/x", httpx.Response(200, content=body))
    with pytest.raises(PermanentSourceError) as err:
        client(provider, clock).get_json("/x", operation="x")
    assert err.value.kind == SourceFailure.BAD_RESPONSE and provider.hits() == 1


def test_links_to_other_hosts_are_refused_before_sending(mock):
    provider, clock = mock
    with pytest.raises(PermanentSourceError, match="refusing to follow"):
        client(provider, clock).get_json("https://api.massive.com/v2/reference/news?cursor=abc", operation="x")
    assert provider.hits() == 0


def test_credentials_never_reach_logs_or_errors(mock, caplog):
    provider, clock = mock
    provider.on("/x", status(401, {"status": "ERROR", "error": "Unknown API Key"}))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(PermanentSourceError) as err:
        client(provider, clock).get_json("/x", operation="x")
    assert "Unknown API Key" in str(err.value)
    assert TEST_KEY not in str(err.value)
    assert TEST_KEY not in caplog.text
    assert "pipeline.source.request" in caplog.text


def test_request_metrics_are_recorded(mock):
    provider, clock = mock
    provider.on("/x", status(429, headers={"Retry-After": "1"}), ok({}))
    c = client(provider, clock)
    c.get_json("/x", operation="x")
    assert c.metrics.value("source_requests_total", provider="mock", status=429) == 1
    assert c.metrics.value("source_requests_total", provider="mock", status=200) == 1
    assert c.metrics.value("source_rate_limit_total", provider="mock") == 1

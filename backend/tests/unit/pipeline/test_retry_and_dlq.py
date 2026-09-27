import base64
import uuid
from datetime import datetime, timezone

import pytest

from app.pipeline.contracts import DeadLetter
from app.pipeline.dlq import dead_letter
from app.pipeline.errors import FailureReason, PermanentError, RetryExhausted, TransientError
from app.pipeline.retry import RetryPolicy, call_with_retry, is_transient
from app.pipeline.serde import decode_dead_letter, encode_dead_letter


class _Flaky:
    def __init__(self, failures, error=TransientError("blip")):
        self.failures, self.error, self.calls = failures, error, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def _run(fn, policy=RetryPolicy(max_attempts=4, base_delay=1, max_delay=5, jitter=False)):
    sleeps = []
    result = call_with_retry(fn, operation="test", policy=policy, sleep=sleeps.append)
    return result, sleeps


def test_transient_failures_are_retried_with_exponential_backoff():
    fn = _Flaky(failures=3)
    result, sleeps = _run(fn)
    assert result == "ok" and fn.calls == 4
    assert sleeps == [1, 2, 4]


def test_backoff_is_capped():
    policy = RetryPolicy(max_attempts=10, base_delay=1, max_delay=5, jitter=False)
    assert [policy.ceiling(n) for n in range(1, 6)] == [1, 2, 4, 5, 5]


def test_full_jitter_stays_within_the_ceiling():
    policy = RetryPolicy(base_delay=1, max_delay=8)
    assert policy.delay(3, rand=lambda: 0.0) == 0.0
    assert policy.delay(3, rand=lambda: 0.999) < policy.ceiling(3) == 4


def test_retries_are_bounded_then_exhausted():
    fn = _Flaky(failures=100)
    with pytest.raises(RetryExhausted) as info:
        _run(fn)
    assert fn.calls == 4
    assert info.value.attempts == 4 and info.value.retry_count == 3
    assert isinstance(info.value.last_error, TransientError)


def test_permanent_errors_are_not_retried():
    fn = _Flaky(failures=1, error=PermanentError(FailureReason.PAYLOAD_INVALID, "bad"))
    with pytest.raises(PermanentError):
        _run(fn)
    assert fn.calls == 1


def test_unclassified_errors_propagate_without_retry():
    fn = _Flaky(failures=1, error=KeyError("bug"))
    with pytest.raises(KeyError):
        _run(fn)
    assert fn.calls == 1


class _HttpError(Exception):
    def __init__(self, status):
        self.response = type("R", (), {"status_code": status})()


@pytest.mark.parametrize(
    "error, transient",
    [
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (_HttpError(429), True),
        (_HttpError(503), True),
        (_HttpError(404), False),
        (_HttpError(400), False),
        (type("OperationalError", (Exception,), {})(), True),
        (ValueError("x"), False),
        (PermanentError(FailureReason.OVERSIZE, "x"), False),
    ],
)
def test_transient_classification(error, transient):
    assert is_transient(error) is transient


def test_boto_style_status_codes_are_classified():
    err = Exception()
    err.response = {"ResponseMetadata": {"HTTPStatusCode": 503}}
    assert is_transient(err)


def test_policy_validates_bounds():
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay=5, max_delay=1)


FIRST = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def test_dead_letter_preserves_original_and_context():
    err = PermanentError(FailureReason.PAYLOAD_INVALID, "close must be > 0", [{"loc": ["record", "close"]}])
    letter = dead_letter(
        component="lake_writer",
        error=err,
        original_value=b'{"schema_version": 1}',
        max_bytes=1_000_000,
        first_failed_at=FIRST,
        source="yfinance.market_daily",
        original_topic="quantpilot.market.raw",
        partition=2,
        offset=41,
        original_key=b"market:SYNTH:1d:2026-09-01:yfinance.market_daily",
    )
    assert letter.failure_reason == "payload_invalid"
    assert letter.error_type == "PermanentError" and letter.retry_count == 0
    assert letter.original_value == '{"schema_version": 1}' and letter.value_encoding == "utf8"
    assert letter.error_details == [{"loc": ["record", "close"]}]
    assert (letter.original_topic, letter.partition, letter.offset) == ("quantpilot.market.raw", 2, 41)
    assert decode_dead_letter(encode_dead_letter(letter)) == letter


def test_dead_letter_records_retry_exhaustion():
    exhausted = RetryExhausted("s3.put", 5, TimeoutError("slow"))
    letter = dead_letter(component="lake_writer", error=exhausted, original_value=b"{}", max_bytes=1_000_000, first_failed_at=FIRST)
    assert letter.failure_reason == FailureReason.RETRIES_EXHAUSTED
    assert letter.retry_count == 4 and letter.error_type == "TimeoutError"


def test_binary_payloads_are_base64_encoded():
    letter = dead_letter(component="lake_writer", error=ValueError("x"), original_value=b"\xff\x00", max_bytes=1_000_000, first_failed_at=FIRST)
    assert letter.value_encoding == "base64"
    assert base64.b64decode(letter.original_value) == b"\xff\x00"


def test_oversize_payloads_are_truncated_and_flagged():
    letter = dead_letter(component="lake_writer", error=ValueError("x"), original_value=b"a" * 50_000, max_bytes=20_000, first_failed_at=FIRST)
    assert letter.original_truncated is True
    assert len(letter.original_value) < 50_000


def test_dead_letters_have_unique_ids():
    a = dead_letter(component="ingestor", error=ValueError("x"), original_value=b"{}", max_bytes=1_000_000, first_failed_at=FIRST)
    b = dead_letter(component="ingestor", error=ValueError("x"), original_value=b"{}", max_bytes=1_000_000, first_failed_at=FIRST)
    assert isinstance(a, DeadLetter) and a.dlq_id != b.dlq_id and isinstance(a.dlq_id, uuid.UUID)

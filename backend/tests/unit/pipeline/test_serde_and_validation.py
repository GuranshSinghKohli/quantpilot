import json
from datetime import datetime, timedelta, timezone

import pytest

from app.pipeline.errors import FailureReason, PermanentError
from app.pipeline.serde import decode_event, encode_event, event_headers
from app.pipeline.validation import check_event

from .factories import INGESTED_AT, company_record, event, market_record, news_record

LIMIT = 1_000_000


def _now():
    return INGESTED_AT + timedelta(minutes=1)


@pytest.mark.parametrize("make", [market_record, news_record, company_record])
def test_round_trip_is_lossless(make):
    ev = event(make())
    decoded = decode_event(encode_event(ev, LIMIT), LIMIT)
    assert decoded == ev
    check_event(decoded, now=_now)


def test_encoding_is_deterministic():
    assert encode_event(event(), LIMIT) == encode_event(event(), LIMIT)


def test_headers_carry_routing_metadata():
    ev = event()
    headers = dict(event_headers(ev))
    assert headers["schema_version"] == b"1"
    assert headers["domain"] == b"market"
    assert headers["event_id"] == ev.event_id.encode()


def _reason(data: bytes) -> FailureReason:
    with pytest.raises(PermanentError) as info:
        decode_event(data, LIMIT)
    return info.value.reason


def _doc(**changes) -> bytes:
    document = json.loads(encode_event(event(), LIMIT))
    for dotted, value in changes.items():
        target = document
        *path, last = dotted.split("__")
        for part in path:
            target = target[part]
        if value is ...:
            del target[last]
        else:
            target[last] = value
    return json.dumps(document).encode()


def test_malformed_bytes_are_deserialize_failures():
    assert _reason(b"\xff\xfe not utf-8") == FailureReason.DESERIALIZE
    assert _reason(b"{not json") == FailureReason.DESERIALIZE
    assert _reason(b"[1, 2]") == FailureReason.DESERIALIZE


def test_unknown_schema_version_is_rejected_explicitly():
    assert _reason(_doc(schema_version=2)) == FailureReason.UNSUPPORTED_SCHEMA_VERSION
    assert _reason(_doc(schema_version=...)) == FailureReason.UNSUPPORTED_SCHEMA_VERSION


def test_envelope_errors_and_payload_errors_are_distinguished():
    assert _reason(_doc(event_id=...)) == FailureReason.ENVELOPE_INVALID
    assert _reason(_doc(domain="weather")) == FailureReason.ENVELOPE_INVALID
    assert _reason(_doc(record__close="-3")) == FailureReason.PAYLOAD_INVALID
    assert _reason(_doc(record__volume=...)) == FailureReason.PAYLOAD_INVALID


def test_errors_carry_field_level_details():
    with pytest.raises(PermanentError) as info:
        decode_event(_doc(record__close="-3"), LIMIT)
    assert any(d["loc"][-1] == "close" for d in info.value.details)


def test_oversize_messages_are_rejected_both_ways():
    ev = event(news_record(summary="x" * 5000))
    with pytest.raises(PermanentError) as info:
        encode_event(ev, 2048)
    assert info.value.reason == FailureReason.OVERSIZE
    assert _reason_with_limit(encode_event(ev, LIMIT), 2048) == FailureReason.OVERSIZE


def _reason_with_limit(data: bytes, limit: int) -> FailureReason:
    with pytest.raises(PermanentError) as info:
        decode_event(data, limit)
    return info.value.reason


def test_tampered_content_is_a_hash_mismatch():
    ev = decode_event(_doc(record__close="106.0"), LIMIT)  # structurally valid, hash now wrong
    with pytest.raises(PermanentError) as info:
        check_event(ev, now=_now)
    assert info.value.reason == FailureReason.HASH_MISMATCH


def test_inconsistent_identities_are_envelope_invalid():
    ev = decode_event(_doc(dedup_key="market:OTHER:1d:2026-09-01:massive.market_daily"), LIMIT)
    with pytest.raises(PermanentError) as info:
        check_event(ev, now=_now)
    assert info.value.reason == FailureReason.ENVELOPE_INVALID
    assert {d["field"] for d in info.value.details} == {"dedup_key"}


def test_future_ingestion_time_is_rejected():
    ev = event(ingested_at=INGESTED_AT)
    with pytest.raises(PermanentError, match="integrity"):
        check_event(ev, now=lambda: INGESTED_AT - timedelta(hours=1))


def test_occurred_after_ingested_is_rejected():
    rec = news_record(published_at=INGESTED_AT + timedelta(hours=2))
    with pytest.raises(PermanentError) as info:
        check_event(event(rec), now=lambda: INGESTED_AT + timedelta(hours=3))
    assert {d["field"] for d in info.value.details} == {"occurred_at"}


def test_occurred_before_1970_is_rejected():
    rec = news_record(published_at=datetime(1969, 12, 31, tzinfo=timezone.utc))
    with pytest.raises(PermanentError):
        check_event(event(rec), now=_now)

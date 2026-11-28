"""The three identities: content_hash (version), dedup_key (logical record),
event_id (observation). Same input must always produce the same keys."""

from datetime import datetime, timedelta, timezone

from app.pipeline import identity

from .factories import company_record, event, market_record, news_record


def test_content_hash_ignores_ingestion_time():
    a = market_record(ingested_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    b = market_record(ingested_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert a.content_hash == b.content_hash


def test_content_hash_is_stable_across_equivalent_inputs():
    est = timezone(timedelta(hours=-4))
    a = market_record(open=100.0, symbol="synth")
    b = market_record(
        open="100.0000001",  # rounds to the same 6 dp value
        symbol="SYNTH",
        timestamp=datetime(2026, 9, 1, 9, 30, tzinfo=est),
    )
    assert a.content_hash == b.content_hash


def test_content_hash_changes_when_content_changes():
    assert market_record(close=105.0).content_hash != market_record(close=105.5).content_hash


def test_content_hash_is_recomputable():
    for record in (market_record(), news_record(), company_record()):
        assert record.compute_content_hash() == record.content_hash


def test_same_record_same_event_id_across_runs():
    import uuid

    a = event(market_record(), run_id=uuid.uuid4())
    b = event(market_record(), run_id=uuid.uuid4())
    assert a.event_id == b.event_id
    assert a.dedup_key == b.dedup_key


def test_new_content_version_keeps_logical_key_but_gets_new_event_id():
    a = event(market_record(close=105.0))
    b = event(market_record(close=105.5))
    assert a.dedup_key == b.dedup_key
    assert a.event_id != b.event_id


def test_news_dedup_key_ignores_tracking_params_and_source():
    a = news_record(url="https://Example.com/synthetic/article-1?utm_source=feed")
    b = news_record(url="https://example.com/synthetic/article-1/", external_id="other-id")
    assert a.dedup_key == b.dedup_key


def test_market_dedup_key_is_vendor_specific():
    key = market_record().dedup_key
    assert key.endswith(":massive.market_daily")


def test_company_dedup_key_uses_padded_cik():
    assert company_record().dedup_key == "company:cik:0000000123"


def test_canonical_json_is_order_independent():
    assert identity.canonical_json({"b": 1, "a": [1, 2]}) == identity.canonical_json({"a": [1, 2], "b": 1})
    assert identity.canonical_json({"s": "é"}) == '{"s":"é"}'


def test_event_id_formula():
    assert identity.event_id("s", "x", "h") == identity.event_id("s", "x", "h")
    assert identity.event_id("s", "x", "h") != identity.event_id("s", "x", "h2")

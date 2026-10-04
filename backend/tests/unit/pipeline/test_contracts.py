from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.pipeline.contracts import SOURCES, CompanyRecord, DataSourceType, Domain, MarketRecord

from .factories import company_record, event, market_record, news_record


def test_market_record_normalizes_numbers_and_symbol():
    r = market_record(symbol=" synth ", open=100.1234567, volume=1000.0)
    assert r.symbol == "SYNTH"
    assert r.open == Decimal("100.123457")
    assert r.volume == Decimal("1000.000000")


def test_timestamps_are_converted_to_utc():
    est = timezone(timedelta(hours=-4))
    r = market_record(timestamp=datetime(2026, 9, 1, 9, 30, tzinfo=est))
    assert r.timestamp == datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    assert r.timestamp.utcoffset() == timedelta(0)


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        market_record(timestamp=datetime(2026, 9, 1, 13, 30))


@pytest.mark.parametrize(
    "overrides",
    [
        {"open": float("nan")},
        {"close": float("inf")},
        {"low": -1.0},
        {"high": 0},
        {"volume": -5},
        {"open": True},
        {"high": 99.0},  # high below open/close
        {"low": 106.0},  # low above open/close
        {"trading_date": date(2026, 8, 20)},  # timestamp from another session
        {"symbol": "not a symbol"},
        {"currency": "usd"},
        {"interval": "1h"},
    ],
)
def test_invalid_market_values_are_rejected(overrides):
    with pytest.raises(ValidationError):
        market_record(**overrides)


def test_unknown_or_mismatched_source_is_rejected():
    with pytest.raises(ValidationError, match="unknown source"):
        market_record(source="made.up")
    with pytest.raises(ValidationError, match="does not produce"):
        market_record(source="massive.news")


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError, match="Extra inputs"):
        market_record(unexpected="x")


def test_news_record_collapses_text_and_dedups_tickers():
    r = news_record(title="  A   spaced\n title ", summary="  ", tickers=["aapl", "AAPL", "msft"])
    assert r.title == "A spaced title"
    assert r.summary is None
    assert r.tickers == ("AAPL", "MSFT")


@pytest.mark.parametrize("url", ["not-a-url", "ftp://example.com/x", "/relative/path"])
def test_news_url_must_be_absolute_http(url):
    with pytest.raises(ValidationError):
        news_record(url=url)


def test_company_cik_is_zero_padded_and_ticker_is_first_listed():
    r = company_record()
    assert r.cik == "0000000123" and r.external_id == "0000000123"
    assert r.ticker == "SYNA" and r.tickers == ("SYNA", "SYNB")
    with pytest.raises(ValidationError, match="first entry"):
        company_record(ticker="SYNB")
    with pytest.raises(ValidationError, match="external_id must equal cik"):
        company_record(external_id="999")
    with pytest.raises(ValidationError, match="CIK"):
        company_record(cik="12ab", external_id="12ab")


def test_records_are_immutable():
    r = market_record()
    with pytest.raises(ValidationError):
        r.close = Decimal("1")  # type: ignore[misc]


def test_build_event_fills_every_derived_identity():
    rec = market_record()
    ev = event(rec)
    assert ev.domain == "market" and ev.source_type == DataSourceType.MARKET_DATA
    assert ev.external_id == "SYNTH:1d:2026-09-01"
    assert ev.dedup_key == "market:SYNTH:1d:2026-09-01:massive.market_daily"
    assert ev.content_hash == rec.content_hash
    assert ev.occurred_at == rec.timestamp
    assert len(ev.event_id) == 64


def test_company_events_have_no_occurred_at():
    assert event(company_record()).occurred_at is None


def test_source_registry_covers_every_domain():
    assert {spec.domain for spec in SOURCES.values()} == set(Domain)
    for key, spec in SOURCES.items():
        assert spec.key == key


def test_record_classes_declare_their_domain():
    assert MarketRecord.DOMAIN == Domain.MARKET
    assert CompanyRecord.DOMAIN == Domain.COMPANY

"""SYNTHETIC test records. Values are made up for tests and are not market data.

Source keys are the real registered adapters because the contracts only accept
registered sources; nothing here is ever written outside a test sandbox.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.pipeline.contracts import CompanyRecord, MarketRecord, NewsRecord, build_event

INGESTED_AT = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
RUN_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def market_record(**overrides) -> MarketRecord:
    fields = dict(
        source="yfinance.market_daily",
        symbol="SYNTH",
        timestamp=datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc),
        trading_date=date(2026, 9, 1),
        open=100.0,
        high=110.0,
        low=95.0,
        close=105.0,
        adj_close=105.0,
        volume=1000,
        currency="USD",
        ingested_at=INGESTED_AT,
    )
    fields.update(overrides)
    return MarketRecord.create(**fields)


def news_record(**overrides) -> NewsRecord:
    fields = dict(
        source="yfinance.news",
        external_id="synthetic-news-1",
        title="Synthetic headline for tests",
        url="https://example.com/synthetic/article-1?utm_source=feed",
        published_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        publisher="Example Wire",
        summary="Synthetic summary.",
        tickers=["synth"],
        ingested_at=INGESTED_AT,
    )
    fields.update(overrides)
    return NewsRecord.create(**fields)


def company_record(**overrides) -> CompanyRecord:
    fields = dict(
        source="sec.company_tickers",
        external_id="123",
        cik="123",
        name="Synthetic Holdings Inc.",
        ticker="SYNA",
        tickers=["SYNA", "SYNB"],
        exchange="Nasdaq",
        ingested_at=INGESTED_AT,
    )
    fields.update(overrides)
    return CompanyRecord.create(**fields)


def event(record=None, **kwargs):
    return build_event(
        record or market_record(),
        run_id=kwargs.pop("run_id", RUN_ID),
        original=kwargs.pop("original", {"synthetic": True}),
        **kwargs,
    )

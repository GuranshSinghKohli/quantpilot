"""Typed rows handed to the canonical upserts (one per processed Parquet row).

Each carries the lineage fields that tie the canonical row back to its raw event:
``source_ingested_at`` and ``event_id`` (newest-wins ordering) and
``processing_job_id`` (the Spark run that produced it).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ingested_at: AwareDatetime
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    processing_job_id: uuid.UUID
    raw_uri: str = Field(min_length=1, max_length=1024)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("*", mode="after")
    @classmethod
    def _utc(cls, value):
        # SQLite compares datetimes as text, so newest-wins needs one offset.
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        return value

    @property
    def version(self) -> tuple:
        """Newest-wins ordering shared with the Spark dedup: later ingestion first,
        then the larger event_id as a deterministic tie-break."""
        return (self.source_ingested_at, self.event_id)


class MarketBarIn(_Row):
    symbol: str = Field(min_length=1, max_length=16)
    interval: str = Field(min_length=1, max_length=8)
    trading_date: date
    bar_ts: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Optional[Decimal] = None
    volume: int = Field(ge=0)
    currency: Optional[str] = Field(default=None, max_length=3)
    source: str = Field(min_length=1, max_length=64)

    @property
    def natural_key(self) -> tuple:
        return (self.symbol, self.interval, self.trading_date, self.source)


class NewsArticleIn(_Row):
    source_key: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    publisher: Optional[str] = Field(default=None, max_length=255)
    author: Optional[str] = Field(default=None, max_length=255)
    published_at: AwareDatetime
    summary: Optional[str] = None
    tickers: List[str] = Field(default_factory=list)


class CompanyListingIn(_Row):
    cik: str = Field(pattern=r"^\d{10}$")
    name: str = Field(min_length=1, max_length=255)
    ticker: Optional[str] = Field(default=None, max_length=16)
    exchange: Optional[str] = Field(default=None, max_length=32)

    @field_validator("ticker", "exchange")
    @classmethod
    def _upper(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip().upper() or None

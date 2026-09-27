"""Ingestion pipeline tables (migration 0005; processing_jobs input columns 0006).

Tracking: ingestion_sources → ingestion_runs → ingestion_records (one row per
ingested event version) and processing_jobs (one row per Spark run).
Canonical data: market_bars (new) and news_articles (1:1 extension of sources).
Companies reuse entities + companies and gain lineage columns.

Lineage: canonical row.event_id → ingestion_records (run, source, raw_uri,
Kafka coordinates) and canonical row.processing_job_id → processing_jobs.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base
from app.domain.enums import (
    IngestionDomain,
    IngestionRecordStatus,
    IngestionRunStatus,
    ProcessingJobStatus,
    ProcessingMode,
)
from app.domain.models import JsonDoc, _created_at, _one_of, _updated_at, _uuid_pk

PRICE = Numeric(20, 6)


def _counter() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, default=0, server_default="0")


class IngestionSource(Base):
    """Registered source adapters. Upserted from the code registry, not seeded by migration."""

    __tablename__ = "ingestion_sources"
    __table_args__ = (_one_of("domain", IngestionDomain, "ck_ingestion_sources_domain"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class IngestionRun(Base):
    """One execution of one source adapter, with producer-side counters.

    Downstream progress (landed / dlq / processed / loaded) is derived from
    ``ingestion_records`` rather than duplicated here."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        _one_of("status", IngestionRunStatus, "ck_ingestion_runs_status"),
        Index("ix_ingestion_runs_source_created", "source_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="RESTRICT"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="cli")
    params: Mapped[Dict[str, Any]] = mapped_column(JsonDoc, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=IngestionRunStatus.PENDING.value
    )
    records_received: Mapped[int] = _counter()
    records_valid: Mapped[int] = _counter()
    records_invalid: Mapped[int] = _counter()
    records_duplicate: Mapped[int] = _counter()
    records_published: Mapped[int] = _counter()
    records_publish_failed: Mapped[int] = _counter()
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()

    source: Mapped[IngestionSource] = relationship()


class ProcessingJob(Base):
    """One Spark processing run over one domain, plus its load into Postgres."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        _one_of("domain", IngestionDomain, "ck_processing_jobs_domain"),
        _one_of("status", ProcessingJobStatus, "ck_processing_jobs_status"),
        _one_of("mode", ProcessingMode, "ck_processing_jobs_mode"),
        Index("ix_processing_jobs_domain_started", "domain", "started_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    since: Mapped[Optional[date]] = mapped_column(Date)
    # Input selection (0006): raw ingestion-date window [since, until], a raw key
    # prefix, or an explicit object list; the manifest lists the objects themselves.
    until: Mapped[Optional[date]] = mapped_column(Date)
    input_prefix: Mapped[Optional[str]] = mapped_column(String(1024))
    input_objects: Mapped[int] = _counter()
    spark_app_id: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_rows: Mapped[int] = _counter()
    rejected_rows: Mapped[int] = _counter()
    duplicates_removed: Mapped[int] = _counter()
    output_rows: Mapped[int] = _counter()
    output_partitions: Mapped[List[str]] = mapped_column(JsonDoc, nullable=False, default=list)
    output_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    manifest_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    code_version: Mapped[Optional[str]] = mapped_column(String(64))
    engine_version: Mapped[Optional[str]] = mapped_column(String(32))
    error: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    loaded_rows: Mapped[Optional[int]] = mapped_column(Integer)
    loaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    load_duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()


class IngestionRecord(Base):
    """One ingested event version (keyed by the deterministic ``event_id``)."""

    __tablename__ = "ingestion_records"
    __table_args__ = (
        _one_of("domain", IngestionDomain, "ck_ingestion_records_domain"),
        _one_of("status", IngestionRecordStatus, "ck_ingestion_records_status"),
        Index("ix_ingestion_records_run_status", "run_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_sources.id", ondelete="RESTRICT"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[Optional[str]] = mapped_column(Text)
    kafka_topic: Mapped[Optional[str]] = mapped_column(String(249))
    kafka_partition: Mapped[Optional[int]] = mapped_column(Integer)
    kafka_offset: Mapped[Optional[int]] = mapped_column(BigInteger)
    raw_uri: Mapped[Optional[str]] = mapped_column(String(1024))
    processing_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = _updated_at()

    run: Mapped[IngestionRun] = relationship()
    source: Mapped[IngestionSource] = relationship()


class _CanonicalLineage:
    """Columns every canonical row carries back to the raw event it came from."""

    source_ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ingestion_records.event_id", ondelete="RESTRICT"), nullable=False
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False
    )


class MarketBar(_CanonicalLineage, Base):
    """Canonical daily bar: one row per (symbol, interval, session, vendor)."""

    __tablename__ = "market_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "trading_date", "source", name="uq_market_bars_bar"),
        CheckConstraint(
            "low > 0 AND low <= high AND volume >= 0", name="ck_market_bars_values"
        ),
        Index("ix_market_bars_trading_date", "trading_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    adj_close: Mapped[Optional[Decimal]] = mapped_column(PRICE)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class NewsArticle(_CanonicalLineage, Base):
    """News-specific fields for a ``sources`` row (source_type='news').

    URL, title, publisher, published_at, content_hash and raw_uri live on the
    source; this table holds only what a generic document lacks."""

    __tablename__ = "news_articles"
    __table_args__ = (Index("ix_news_articles_source_external", "source_key", "external_id"),)

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(255))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    tickers: Mapped[List[str]] = mapped_column(JsonDoc, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

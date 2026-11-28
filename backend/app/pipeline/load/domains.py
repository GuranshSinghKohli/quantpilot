"""Explicit processed-column → canonical-field mapping, one loader per domain.

Each loader turns one processed Parquet row into the Phase 2.2 typed row
(``*In``), states the domain rules the canonical tables rely on, recomputes the
contract identity (``dedup_key``) so Spark and Postgres cannot disagree about
which rows are the same record, and writes through ``app.repositories.canonical``.
None of them reads raw data or decides versions: that is ``newer_than_existing``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Sequence, Type

from sqlalchemy.orm import Session

from app.domain.identity import normalize_url
from app.domain.ingestion_schemas import CompanyListingIn, MarketBarIn, NewsArticleIn, _Row
from app.pipeline import identity
from app.repositories import canonical
from app.repositories.canonical import UpsertStats


def _lineage(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source_ingested_at": rec["ingested_at"],
        "event_id": rec["event_id"],
        "processing_job_id": rec["processing_job_id"],
        "raw_uri": rec["raw_uri"],
        "content_hash": rec["content_hash"],
    }


class DomainLoader:
    domain: ClassVar[str]
    model: ClassVar[Type[_Row]]
    table: ClassVar[str]

    def to_row(self, rec: Dict[str, Any]) -> _Row:
        raise NotImplementedError

    def dedup_key(self, row: _Row) -> str:
        raise NotImplementedError

    def problems(self, row: _Row) -> List[str]:
        return []

    def upsert(self, db: Session, rows: Sequence[_Row]) -> UpsertStats:
        raise NotImplementedError

    def failure_key(self, row: _Row) -> str:
        raise NotImplementedError


class MarketLoader(DomainLoader):
    domain = "market"
    model = MarketBarIn
    table = "market_bars"

    def to_row(self, rec: Dict[str, Any]) -> MarketBarIn:
        return MarketBarIn(
            **_lineage(rec),
            symbol=rec["symbol"],
            interval=rec["interval"],
            trading_date=rec["trading_date"],
            bar_ts=rec["bar_timestamp"],
            open=rec["open"],
            high=rec["high"],
            low=rec["low"],
            close=rec["close"],
            adj_close=rec["adj_close"],
            volume=rec["volume"],
            currency=rec["currency"],
            source=rec["source"],
        )

    def dedup_key(self, row: MarketBarIn) -> str:
        return identity.market_dedup_key(row.symbol, row.interval, row.trading_date, row.source)

    def problems(self, row: MarketBarIn) -> List[str]:
        # ck_market_bars (low > 0, low <= high, volume >= 0) plus the OHLC range Spark enforced.
        found = []
        if row.low <= 0:
            found.append(f"low {row.low} is not positive")
        if row.low > row.high:
            found.append(f"low {row.low} > high {row.high}")
        for name in ("open", "close"):
            value = getattr(row, name)
            if not row.low <= value <= row.high:
                found.append(f"{name} {value} outside [low {row.low}, high {row.high}]")
        if row.adj_close is not None and row.adj_close <= 0:
            found.append(f"adj_close {row.adj_close} is not positive")
        return found

    def upsert(self, db: Session, rows: Sequence[MarketBarIn]) -> UpsertStats:
        return canonical.upsert_market_bars(db, rows)

    def failure_key(self, row: MarketBarIn) -> str:
        return ":".join(str(p) for p in row.natural_key)


class NewsLoader(DomainLoader):
    domain = "news"
    model = NewsArticleIn
    table = "news_articles+sources"

    def to_row(self, rec: Dict[str, Any]) -> NewsArticleIn:
        return NewsArticleIn(
            **_lineage(rec),
            source_key=rec["source"],
            external_id=rec["external_id"],
            url=rec["url"],
            title=rec["title"],
            publisher=rec["publisher"],
            author=rec["author"],
            published_at=rec["published_at"],
            summary=rec["summary"],
            tickers=list(rec["tickers"] or []),
        )

    def dedup_key(self, row: NewsArticleIn) -> str:
        return identity.news_dedup_key(row.url)

    def problems(self, row: NewsArticleIn) -> List[str]:
        try:
            normalize_url(row.url)
        except ValueError as exc:
            return [str(exc)]
        if not row.url.lower().startswith(("http://", "https://")):
            return [f"url {row.url!r} is not http(s)"]
        return []

    def upsert(self, db: Session, rows: Sequence[NewsArticleIn]) -> UpsertStats:
        return canonical.upsert_news(db, rows)

    def failure_key(self, row: NewsArticleIn) -> str:
        return row.url


class CompanyLoader(DomainLoader):
    domain = "company"
    model = CompanyListingIn
    table = "companies+entities"

    def to_row(self, rec: Dict[str, Any]) -> CompanyListingIn:
        return CompanyListingIn(
            **_lineage(rec),
            cik=rec["cik"],
            name=rec["name"],
            ticker=rec["ticker"],
            tickers=list(rec["tickers"] or []),
            exchange=rec["exchange"],
            source=rec["source"],
        )

    def dedup_key(self, row: CompanyListingIn) -> str:
        return identity.company_dedup_key(row.cik)

    def problems(self, row: CompanyListingIn) -> List[str]:
        expected = row.tickers[0] if row.tickers else None
        if row.ticker != expected:
            return [f"ticker {row.ticker!r} is not the first of tickers {row.tickers}"]
        return []

    def upsert(self, db: Session, rows: Sequence[CompanyListingIn]) -> UpsertStats:
        return canonical.upsert_companies(db, rows)

    def failure_key(self, row: CompanyListingIn) -> str:
        return f"cik:{row.cik}"


LOADERS: Dict[str, DomainLoader] = {loader.domain: loader for loader in (CompanyLoader(), MarketLoader(), NewsLoader())}

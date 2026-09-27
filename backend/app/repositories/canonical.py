"""Canonical upserts for pipeline output: market bars, news, companies.

Newest wins by ``(source_ingested_at, event_id)``, the same ordering the Spark
dedup uses, so replaying older data never overwrites newer data and reloading
the same data changes nothing. Flush, never commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import EntityType, SourceType
from app.domain.identity import normalize_url, url_hash
from app.domain.ingestion_models import MarketBar, NewsArticle
from app.domain.ingestion_schemas import CompanyListingIn, MarketBarIn, NewsArticleIn
from app.domain.models import Company, Entity, Source
from app.repositories._upsert import chunks, dialect_insert, newer_than_existing, newest_per_key

_BATCH = 500


@dataclass
class UpsertStats:
    received: int = 0
    written: int = 0  # inserted or updated to a newer version
    unchanged: int = 0  # same or older version already stored
    failed: List[Dict[str, str]] = field(default_factory=list)

    def merge(self, other: "UpsertStats") -> "UpsertStats":
        return UpsertStats(
            self.received + other.received,
            self.written + other.written,
            self.unchanged + other.unchanged,
            self.failed + other.failed,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --- market bars -------------------------------------------------------------------

_BAR_KEY = ["symbol", "interval", "trading_date", "source"]
_BAR_UPDATABLE = [
    "bar_ts", "open", "high", "low", "close", "adj_close", "volume", "currency",
    "content_hash", "raw_uri", "source_ingested_at", "event_id", "processing_job_id",
]


def upsert_market_bars(db: Session, rows: Sequence[MarketBarIn]) -> UpsertStats:
    stats = UpsertStats(received=len(rows))
    latest = newest_per_key(rows, key=lambda r: r.natural_key, version=lambda r: r.version)
    stats.unchanged += len(rows) - len(latest)
    table = MarketBar.__table__
    insert = dialect_insert(db)
    for batch in chunks(latest, _BATCH):
        values = [{"id": uuid.uuid4(), **row.model_dump()} for row in batch]
        stmt = insert(table).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=_BAR_KEY,
            set_={**{c: stmt.excluded[c] for c in _BAR_UPDATABLE}, "updated_at": func.now()},
            where=newer_than_existing(stmt.excluded, table),
        ).returning(table.c.id)
        written = len(db.execute(stmt).all())
        stats.written += written
        stats.unchanged += len(batch) - written
    return stats


# --- news ----------------------------------------------------------------------


def upsert_news(db: Session, rows: Sequence[NewsArticleIn]) -> UpsertStats:
    """A news article is a ``sources`` row (the document) plus a ``news_articles``
    row (news-only fields). Identity is the normalized URL hash."""
    stats = UpsertStats(received=len(rows))
    latest = newest_per_key(rows, key=lambda r: url_hash(r.url), version=lambda r: r.version)
    stats.unchanged += len(rows) - len(latest)
    insert = dialect_insert(db)
    sources, articles = Source.__table__, NewsArticle.__table__

    for batch in chunks(latest, _BATCH):
        candidates = {url_hash(r.url): r for r in batch}
        stored = {
            h: (_aware(ts), eid)
            for h, ts, eid in db.execute(
                select(Source.url_hash, NewsArticle.source_ingested_at, NewsArticle.event_id)
                .join(NewsArticle, NewsArticle.source_id == Source.id)
                .where(Source.url_hash.in_(list(candidates)))
            )
        }
        # Decide the winner once so the sources row and its news_articles row
        # always come from the same event version.
        hashes = {h: r for h, r in candidates.items() if h not in stored or r.version > stored[h]}
        stats.unchanged += len(candidates) - len(hashes)
        if not hashes:
            continue
        source_values = [
            {
                "id": uuid.uuid4(),
                "source_type": SourceType.NEWS.value,
                "publisher": r.publisher,
                "url": normalize_url(r.url),
                "url_hash": h,
                "title": r.title,
                "published_at": r.published_at,
                "retrieved_at": r.source_ingested_at,
                "content_hash": r.content_hash,
                "raw_uri": r.raw_uri,
                "metadata": {},
            }
            for h, r in hashes.items()
        ]
        stmt = insert(sources).values(source_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["url_hash"],
            set_={c: stmt.excluded[c] for c in ("publisher", "title", "published_at", "retrieved_at", "content_hash", "raw_uri")},
            where=stmt.excluded.retrieved_at >= sources.c.retrieved_at,
        )
        db.execute(stmt)
        ids = dict(db.execute(select(Source.url_hash, Source.id).where(Source.url_hash.in_(list(hashes)))).all())

        article_values = [
            {
                "source_id": ids[h],
                "source_key": r.source_key,
                "external_id": r.external_id,
                "author": r.author,
                "summary": r.summary,
                "tickers": list(r.tickers),
                "source_ingested_at": r.source_ingested_at,
                "event_id": r.event_id,
                "processing_job_id": r.processing_job_id,
            }
            for h, r in hashes.items()
        ]
        stmt = insert(articles).values(article_values)
        updatable = ["source_key", "external_id", "author", "summary", "tickers", "source_ingested_at", "event_id", "processing_job_id"]
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id"],
            set_={**{c: stmt.excluded[c] for c in updatable}, "updated_at": func.now()},
            where=newer_than_existing(stmt.excluded, articles),
        ).returning(articles.c.source_id)
        written = len(db.execute(stmt).all())
        stats.written += written
        stats.unchanged += len(hashes) - written
    return stats


# --- companies -------------------------------------------------------------------


def upsert_companies(db: Session, rows: Sequence[CompanyListingIn]) -> UpsertStats:
    """Companies resolve strictly by CIK (the SEC's stable identifier).

    A new company whose name is already taken by a different CIK gets a
    disambiguated entity name ("<name> (CIK <cik>)") instead of being merged.
    A row that violates a constraint (e.g. a ticker+exchange held by another
    CIK) is reported in ``failed`` and does not abort the batch."""
    stats = UpsertStats(received=len(rows))
    latest = newest_per_key(rows, key=lambda r: r.cik, version=lambda r: r.version)
    stats.unchanged += len(rows) - len(latest)

    for batch in chunks(latest, _BATCH):
        existing = {c.cik: c for c in db.scalars(select(Company).where(Company.cik.in_([r.cik for r in batch])))}
        taken = set(
            db.scalars(
                select(Entity.canonical_name).where(
                    Entity.entity_type == EntityType.COMPANY.value,
                    Entity.canonical_name.in_([r.name for r in batch]),
                )
            )
        )
        for row in batch:
            try:
                with db.begin_nested():
                    changed = _write_company(db, row, existing.get(row.cik), taken)
                    db.flush()
            except IntegrityError as exc:
                stats.failed.append({"cik": row.cik, "event_id": row.event_id, "error": str(exc.orig)[:500]})
                continue
            if changed:
                stats.written += 1
            else:
                stats.unchanged += 1
    return stats


def _write_company(db: Session, row: CompanyListingIn, company, taken: set) -> bool:
    lineage = {
        "source_ingested_at": row.source_ingested_at,
        "event_id": row.event_id,
        "processing_job_id": row.processing_job_id,
    }
    if company is None:
        name = row.name if row.name not in taken else f"{row.name} (CIK {row.cik})"[:255]
        taken.add(name)
        entity = Entity(id=uuid.uuid4(), entity_type=EntityType.COMPANY.value, canonical_name=name)
        db.add(
            Company(
                entity=entity,
                legal_name=row.name,
                ticker=row.ticker,
                exchange=row.exchange,
                cik=row.cik,
                **lineage,
            )
        )
        return True
    stored: Tuple = (_aware(company.source_ingested_at), company.event_id or "") if company.source_ingested_at else ()
    if stored and row.version <= stored:
        return False
    company.legal_name = row.name
    company.ticker = row.ticker
    company.exchange = row.exchange
    for key, value in lineage.items():
        setattr(company, key, value)
    return True

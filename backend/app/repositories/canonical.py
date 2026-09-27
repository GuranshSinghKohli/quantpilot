"""Canonical upserts for pipeline output: market bars, news, companies.

Newest wins by ``(source_ingested_at, event_id)`` (``newer_than_existing``; the
Spark dedup applies the same order), so replaying older data never overwrites
newer data and reloading the same data changes nothing. Correctness comes from
the guarded ``ON CONFLICT`` statements and the table constraints; the stored
versions read before each batch are only used to classify outcomes. Flush,
never commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Tuple, TypeVar

from sqlalchemy import bindparam, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import EntityType, SourceType
from app.domain.identity import normalize_url, url_hash
from app.domain.ingestion_models import MarketBar, NewsArticle
from app.domain.ingestion_schemas import CompanyListingIn, MarketBarIn, NewsArticleIn, _Row
from app.domain.models import Company, Entity, Source
from app.repositories._upsert import chunks, dialect_insert, newer_than_existing

_BATCH = 500

R = TypeVar("R", bound=_Row)
Version = Tuple[datetime, str]


class OutcomeConflict(RuntimeError):
    """The guarded write disagreed with the versions read in the same transaction:
    another writer changed the rows in between. Roll back and retry."""


@dataclass
class UpsertStats:
    """Every received row lands in exactly one of inserted / updated / stale /
    duplicate / failed. ``current`` lists the event ids that are the stored
    version after the call (inserted, updated or duplicate)."""

    received: int = 0
    inserted: int = 0
    updated: int = 0
    stale: int = 0  # an equal-or-newer different version is stored (or won inside the batch)
    duplicate: int = 0  # this exact event is already the stored version
    failed: List[Dict[str, str]] = field(default_factory=list)
    failed_rows: List[Any] = field(default_factory=list, repr=False)
    current: List[str] = field(default_factory=list, repr=False)

    @property
    def written(self) -> int:
        return self.inserted + self.updated

    @property
    def unchanged(self) -> int:
        return self.stale + self.duplicate

    @property
    def accounted(self) -> int:
        return self.inserted + self.updated + self.stale + self.duplicate + len(self.failed)

    def merge(self, other: "UpsertStats") -> "UpsertStats":
        return UpsertStats(
            self.received + other.received,
            self.inserted + other.inserted,
            self.updated + other.updated,
            self.stale + other.stale,
            self.duplicate + other.duplicate,
            self.failed + other.failed,
            self.failed_rows + other.failed_rows,
            self.current + other.current,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _collapse(rows: Sequence[R], key: Callable[[R], Hashable], stats: UpsertStats) -> Dict[Hashable, R]:
    """Newest row per key (one statement may not touch a conflict target twice).
    Losers are counted here: the same event again is a duplicate, else stale."""
    best: Dict[Hashable, R] = {}
    for row in rows:
        k = key(row)
        held = best.get(k)
        if held is None:
            best[k] = row
            continue
        loser = row
        if row.version > held.version:
            best[k], loser = row, held
        if loser.event_id == best[k].event_id:
            stats.duplicate += 1
        else:
            stats.stale += 1
    return best


def _classify(
    latest: Dict[Hashable, R], stored: Dict[Hashable, Version], written: set, stats: UpsertStats
) -> None:
    for k, row in latest.items():
        before = stored.get(k)
        if k in written:
            if before is not None and before >= row.version:
                raise OutcomeConflict(f"{k}: written over stored version {before}")
            stats.current.append(row.event_id)
            if before is None:
                stats.inserted += 1
            else:
                stats.updated += 1
            continue
        if before is None or before < row.version:
            raise OutcomeConflict(f"{k}: newer version {row.version} not written over {before}")
        if before[1] == row.event_id:
            stats.duplicate += 1
            stats.current.append(row.event_id)
        else:
            stats.stale += 1


# --- market bars -------------------------------------------------------------------

_BAR_KEY = ["symbol", "interval", "trading_date", "source"]
_BAR_UPDATABLE = [
    "bar_ts", "open", "high", "low", "close", "adj_close", "volume", "currency",
    "content_hash", "raw_uri", "source_ingested_at", "event_id", "processing_job_id",
]


def _bar_upsert(insert, table, key_cols):
    # Executed with a list of parameter sets: SQLAlchemy batches them into
    # multi-row INSERTs ("insertmanyvalues") from one cached compiled statement.
    stmt = insert(table)
    return stmt.on_conflict_do_update(
        index_elements=_BAR_KEY,
        set_={**{c: stmt.excluded[c] for c in _BAR_UPDATABLE}, "updated_at": func.now()},
        where=newer_than_existing(stmt.excluded, table),
    ).returning(*key_cols)


def upsert_market_bars(db: Session, rows: Sequence[MarketBarIn]) -> UpsertStats:
    stats = UpsertStats(received=len(rows))
    latest = _collapse(rows, lambda r: r.natural_key, stats)
    table = MarketBar.__table__
    key_cols = [table.c[c] for c in _BAR_KEY]
    insert = dialect_insert(db)
    for batch_keys in chunks(list(latest), _BATCH):
        stored = {
            (sym, iv, day, src): (_aware(ts), eid)
            for sym, iv, day, src, ts, eid in db.execute(
                select(*key_cols, table.c.source_ingested_at, table.c.event_id).where(tuple_(*key_cols).in_(batch_keys))
            )
        }
        values = [{"id": uuid.uuid4(), **latest[k].model_dump()} for k in batch_keys]
        written = {tuple(r) for r in db.execute(_bar_upsert(insert, table, key_cols), values).all()}
        _classify({k: latest[k] for k in batch_keys}, stored, written, stats)
    return stats


# --- news ----------------------------------------------------------------------

_SOURCE_DOC = ("url", "publisher", "title", "published_at", "retrieved_at", "content_hash", "raw_uri")
_ARTICLE_UPDATABLE = [
    "source_key", "external_id", "author", "summary", "tickers", "source_ingested_at", "event_id", "processing_job_id",
]


def _source_doc(row: NewsArticleIn) -> Dict[str, Any]:
    return {
        "url": normalize_url(row.url),
        "publisher": row.publisher,
        "title": row.title,
        "published_at": row.published_at,
        "retrieved_at": row.source_ingested_at,
        "content_hash": row.content_hash,
        "raw_uri": row.raw_uri,
    }


def upsert_news(db: Session, rows: Sequence[NewsArticleIn]) -> UpsertStats:
    """A news article is a ``sources`` row (the document) plus a ``news_articles``
    row (news-only fields). Identity is the normalized URL hash.

    The version decision is the guarded ``news_articles`` upsert alone; the
    ``sources`` document fields are then copied from exactly the rows it wrote,
    in the same transaction, so both rows always carry the same event version.
    """
    stats = UpsertStats(received=len(rows))
    latest = _collapse(rows, lambda r: url_hash(r.url), stats)
    insert = dialect_insert(db)
    sources, articles = Source.__table__, NewsArticle.__table__

    for hashes in chunks(list(latest), _BATCH):
        stored = {
            h: (_aware(ts), eid)
            for h, ts, eid in db.execute(
                select(Source.url_hash, NewsArticle.source_ingested_at, NewsArticle.event_id)
                .join(NewsArticle, NewsArticle.source_id == Source.id)
                .where(Source.url_hash.in_(hashes))
            )
        }
        new_sources = [
            {"id": uuid.uuid4(), "source_type": SourceType.NEWS.value, "url_hash": h, "metadata": {}, **_source_doc(latest[h])}
            for h in hashes
        ]
        db.execute(insert(sources).on_conflict_do_nothing(index_elements=["url_hash"]), new_sources)
        ids = dict(db.execute(select(Source.url_hash, Source.id).where(Source.url_hash.in_(hashes))).all())

        article_values = [
            {
                "source_id": ids[h],
                "source_key": latest[h].source_key,
                "external_id": latest[h].external_id,
                "author": latest[h].author,
                "summary": latest[h].summary,
                "tickers": list(latest[h].tickers),
                "source_ingested_at": latest[h].source_ingested_at,
                "event_id": latest[h].event_id,
                "processing_job_id": latest[h].processing_job_id,
            }
            for h in hashes
        ]
        stmt = insert(articles)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id"],
            set_={**{c: stmt.excluded[c] for c in _ARTICLE_UPDATABLE}, "updated_at": func.now()},
            where=newer_than_existing(stmt.excluded, articles),
        ).returning(articles.c.source_id)
        by_id = {v: h for h, v in ids.items()}
        written = {by_id[source_id] for (source_id,) in db.execute(stmt, article_values).all()}

        if written:
            update = (
                sources.update()
                .where(sources.c.id == bindparam("b_id"))
                .values({c: bindparam(f"b_{c}") for c in _SOURCE_DOC})
            )
            db.connection().execute(
                update,
                [{"b_id": ids[h], **{f"b_{c}": v for c, v in _source_doc(latest[h]).items()}} for h in written],
            )
        _classify({h: latest[h] for h in hashes}, stored, written, stats)
    return stats


# --- companies -------------------------------------------------------------------


def upsert_companies(db: Session, rows: Sequence[CompanyListingIn]) -> UpsertStats:
    """Companies resolve strictly by CIK (the SEC's stable identifier).

    Existing rows are locked (``FOR UPDATE`` on Postgres) before the version
    comparison, so the compare-and-write cannot race another writer. A new
    company whose name is already taken by a different CIK gets a disambiguated
    entity name ("<name> (CIK <cik>)") instead of being merged. A row that
    violates a constraint (e.g. a ticker+exchange held by another CIK) is
    reported in ``failed`` with the row in ``failed_rows``; it does not abort
    the batch."""
    stats = UpsertStats(received=len(rows))
    latest = _collapse(rows, lambda r: r.cik, stats)

    for ciks in chunks(list(latest), _BATCH):
        # One savepoint for the chunk; only if a constraint fails is the chunk
        # redone row by row, so one bad row cannot take the others down.
        try:
            with db.begin_nested():
                outcomes = _write_companies(db, [latest[c] for c in ciks], isolate=False)
        except IntegrityError:
            outcomes = _write_companies(db, [latest[c] for c in ciks], isolate=True)
        for row, kind, error in outcomes:
            if kind == "failed":
                stats.failed.append({"cik": row.cik, "event_id": row.event_id, "error": error})
                stats.failed_rows.append(row)
                continue
            setattr(stats, kind, getattr(stats, kind) + 1)
            if kind != "stale":
                stats.current.append(row.event_id)
    return stats


def _write_companies(db: Session, rows: List[CompanyListingIn], *, isolate: bool) -> List[Tuple[Any, str, Optional[str]]]:
    """(row, inserted|updated|duplicate|stale|failed, error) per row. Without
    ``isolate`` a constraint error propagates (the caller's savepoint undoes
    the chunk); with it each row gets its own savepoint."""
    existing = {
        c.cik: c
        for c in db.scalars(
            select(Company)
            .where(Company.cik.in_([r.cik for r in rows]))
            .order_by(Company.cik)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }
    taken = set(
        db.scalars(
            select(Entity.canonical_name).where(
                Entity.entity_type == EntityType.COMPANY.value,
                Entity.canonical_name.in_([r.name for r in rows]),
            )
        )
    )
    outcomes = []
    for row in rows:
        company = existing.get(row.cik)
        before = _company_version(company)
        if isolate:
            try:
                with db.begin_nested():
                    changed = _write_company(db, row, company, before, taken)
                    db.flush()
            except IntegrityError as exc:
                outcomes.append((row, "failed", str(exc.orig)[:500]))
                if company is not None:
                    db.refresh(company)
                continue
        else:
            changed = _write_company(db, row, company, before, taken)
        if changed:
            kind = "inserted" if company is None else "updated"
        elif before is not None and before[1] == row.event_id:
            kind = "duplicate"
        else:
            kind = "stale"
        outcomes.append((row, kind, None))
    if not isolate:
        db.flush()
    return outcomes


def _company_version(company: Optional[Company]) -> Optional[Version]:
    if company is None or company.source_ingested_at is None:
        return None
    return (_aware(company.source_ingested_at), company.event_id or "")


def _write_company(db: Session, row: CompanyListingIn, company, before: Optional[Version], taken: set) -> bool:
    fields = {
        "legal_name": row.name,
        "ticker": row.ticker,
        "tickers": list(row.tickers),
        "exchange": row.exchange,
        "source": row.source,
        "content_hash": row.content_hash,
        "raw_uri": row.raw_uri,
        "source_ingested_at": row.source_ingested_at,
        "event_id": row.event_id,
        "processing_job_id": row.processing_job_id,
    }
    if company is None:
        name = row.name if row.name not in taken else f"{row.name} (CIK {row.cik})"[:255]
        taken.add(name)
        entity = Entity(id=uuid.uuid4(), entity_type=EntityType.COMPANY.value, canonical_name=name)
        db.add(Company(entity=entity, cik=row.cik, **fields))
        return True
    if before is not None and row.version <= before:
        return False
    for key, value in fields.items():
        setattr(company, key, value)
    return True

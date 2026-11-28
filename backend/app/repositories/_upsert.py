"""Dialect-aware INSERT ... ON CONFLICT helpers (Postgres and SQLite)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Iterator, List, Sequence, Type, TypeVar

from sqlalchemy import and_, or_
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.db.models import Base

T = TypeVar("T")


def dialect_insert(db: Session) -> Callable:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql.insert
    if dialect == "sqlite":
        return sqlite.insert
    raise NotImplementedError(f"ON CONFLICT is not supported for {dialect}")


def insert_ignore(
    db: Session,
    model: Type[Base],
    values: Dict[str, Any],
    conflict_columns: Sequence[str],
) -> bool:
    """Insert one row unless it collides on ``conflict_columns``. True if inserted."""
    table = model.__table__
    pk = list(table.primary_key.columns)[0]
    stmt = (
        dialect_insert(db)(table)
        .values(**values)
        .on_conflict_do_nothing(index_elements=list(conflict_columns))
        .returning(pk)
    )
    return db.execute(stmt).first() is not None


def newer_than_existing(excluded, table, ts_column: str = "source_ingested_at", tie_column: str = "event_id"):
    """WHERE clause for newest-wins upserts: (ingested_at, event_id) strictly greater."""
    new_ts, old_ts = excluded[ts_column], table.c[ts_column]
    return or_(
        old_ts.is_(None),
        new_ts > old_ts,
        and_(new_ts == old_ts, excluded[tie_column] > table.c[tie_column]),
    )


def newest_per_key(rows: Iterable[T], key: Callable[[T], Any], version: Callable[[T], Any]) -> List[T]:
    """Collapse rows sharing a natural key to the newest version. One statement
    may not touch the same conflict target twice (Postgres rejects it)."""
    best: Dict[Any, T] = {}
    for row in rows:
        k = key(row)
        if k not in best or version(row) > version(best[k]):
            best[k] = row
    return list(best.values())


def chunks(items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]

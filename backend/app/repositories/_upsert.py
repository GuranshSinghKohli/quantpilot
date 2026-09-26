"""Dialect-aware INSERT ... ON CONFLICT DO NOTHING."""

from __future__ import annotations

from typing import Any, Dict, Sequence, Type

from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.db.models import Base


def insert_ignore(
    db: Session,
    model: Type[Base],
    values: Dict[str, Any],
    conflict_columns: Sequence[str],
) -> bool:
    """Insert one row unless it collides on ``conflict_columns``. True if inserted."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgresql.insert
    elif dialect == "sqlite":
        insert = sqlite.insert
    else:
        raise NotImplementedError(f"insert_ignore does not support {dialect}")
    table = model.__table__
    pk = list(table.primary_key.columns)[0]
    stmt = (
        insert(table)
        .values(**values)
        .on_conflict_do_nothing(index_elements=list(conflict_columns))
        .returning(pk)
    )
    return db.execute(stmt).first() is not None

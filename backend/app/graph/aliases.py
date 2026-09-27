"""Idempotent alias writes. Tickers are attributes, not identity."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.domain.enums import AliasType
from app.domain.models import Entity, EntityAlias
from app.graph.normalize import normalize_name, normalize_ticker
from app.repositories._upsert import insert_ignore


def upsert_alias(
    db: Session,
    entity_id: uuid.UUID,
    alias: str,
    alias_type: AliasType,
    *,
    source_id: Optional[uuid.UUID] = None,
    confidence: float = 1.0,
) -> bool:
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("alias confidence must be between 0 and 1")
    raw = alias.strip()
    if not raw:
        raise ValueError("alias must not be empty")
    normalized = normalize_ticker(raw) if alias_type == AliasType.TICKER else normalize_name(raw)
    if not normalized:
        raise ValueError("normalized alias must not be empty")
    return insert_ignore(
        db,
        EntityAlias,
        {
            "id": uuid.uuid4(),
            "entity_id": entity_id,
            "alias": raw if alias_type != AliasType.TICKER else normalized,
            "normalized_alias": normalized,
            "alias_type": alias_type.value,
            "source_id": source_id,
            "confidence": confidence,
        },
        ["entity_id", "alias_type", "normalized_alias"],
    )


def sync_company_aliases(db: Session, entity: Entity, *, legal_name: str, tickers: list[str]) -> None:
    entity.normalized_name = normalize_name(entity.canonical_name)
    upsert_alias(db, entity.id, legal_name, AliasType.LEGAL_NAME)
    upsert_alias(db, entity.id, entity.canonical_name, AliasType.DISPLAY_NAME)
    for symbol in tickers:
        if symbol:
            upsert_alias(db, entity.id, symbol, AliasType.TICKER)

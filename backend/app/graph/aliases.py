"""Idempotent alias writes. Tickers are attributes, not identity."""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.domain.enums import AliasType
from app.domain.models import Company, Entity, EntityAlias
from app.graph.normalize import legal_name_stem, normalize_name, normalize_ticker
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


def _ticker_list(company: Optional[Company], extra: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for symbol in extra:
        if symbol and symbol not in seen:
            seen.append(symbol)
    if company is None:
        return seen
    if company.ticker and company.ticker not in seen:
        seen.append(company.ticker)
    for symbol in company.tickers or []:
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen


def sync_company_aliases(
    db: Session,
    entity: Entity,
    *,
    legal_name: str,
    tickers: list[str],
    display_name: Optional[str] = None,
    source_name: Optional[str] = None,
) -> None:
    """Idempotent aliases from authoritative structured fields only."""
    entity.normalized_name = normalize_name(entity.canonical_name)
    upsert_alias(db, entity.id, legal_name, AliasType.LEGAL_NAME)
    display = display_name or entity.canonical_name
    upsert_alias(db, entity.id, display, AliasType.DISPLAY_NAME)
    stem = legal_name_stem(legal_name)
    if stem and normalize_name(stem) != normalize_name(legal_name):
        upsert_alias(db, entity.id, stem, AliasType.DISPLAY_NAME)
    if source_name and normalize_name(source_name) not in {
        normalize_name(legal_name),
        normalize_name(display),
    }:
        upsert_alias(db, entity.id, source_name, AliasType.SOURCE_ALIAS)
    for symbol in _ticker_list(entity.company, tickers):
        upsert_alias(db, entity.id, symbol, AliasType.TICKER)


def enrich_company_aliases(db: Session, company: Company) -> None:
    """Re-apply structured aliases for an existing company row."""
    entity = company.entity or db.get(Entity, company.entity_id)
    if entity is None:
        return
    sync_company_aliases(
        db,
        entity,
        legal_name=company.legal_name,
        tickers=list(company.tickers or ([company.ticker] if company.ticker else [])),
        display_name=entity.canonical_name,
        source_name=company.source if company.source and company.source.lower() not in {"sec"} else None,
    )

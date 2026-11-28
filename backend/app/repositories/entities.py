"""Entities and their typed extensions (companies, technologies)."""

from __future__ import annotations

import uuid
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import EntityType
from app.domain.models import Company, Entity, Technology
from app.domain.schemas import CompanyIn, TechnologyIn
from app.graph.aliases import sync_company_aliases
from app.graph.normalize import normalize_name


def _find_company(db: Session, data: CompanyIn) -> Optional[Company]:
    if data.cik:
        found = db.scalars(select(Company).where(Company.cik == data.cik)).first()
        if found:
            return found
    if data.ticker:
        stmt = select(Company).where(Company.ticker == data.ticker)
        stmt = stmt.where(
            Company.exchange == data.exchange if data.exchange else Company.exchange.is_(None)
        )
        found = db.scalars(stmt).first()
        if found:
            return found
    return db.scalars(
        select(Company)
        .join(Entity, Entity.id == Company.entity_id)
        .where(
            Entity.entity_type == EntityType.COMPANY.value,
            Entity.canonical_name == data.legal_name,
        )
    ).first()


def get_or_create_company(db: Session, data: CompanyIn) -> Tuple[Company, bool]:
    """Resolve by CIK, then ticker + exchange, then exact legal name."""
    existing = _find_company(db, data)
    if existing:
        return existing, False
    try:
        with db.begin_nested():
            entity = Entity(
                id=uuid.uuid4(),
                entity_type=EntityType.COMPANY.value,
                canonical_name=data.legal_name,
                normalized_name=normalize_name(data.legal_name),
                source_system="sec" if data.cik else None,
                source_key=data.cik,
            )
            tickers = [data.ticker] if data.ticker else []
            company = Company(
                entity=entity,
                legal_name=data.legal_name,
                ticker=data.ticker,
                exchange=data.exchange,
                cik=data.cik,
                website=data.website,
                tickers=tickers or None,
            )
            db.add(company)
            db.flush()
            sync_company_aliases(db, entity, legal_name=data.legal_name, tickers=tickers)
            db.flush()
        return company, True
    except IntegrityError:
        # A concurrent writer created the same company between lookup and insert.
        found = _find_company(db, data)
        if found is None:
            raise
        return found, False


def get_or_create_technology(db: Session, data: TechnologyIn) -> Tuple[Technology, bool]:
    existing = db.scalars(select(Technology).where(Technology.slug == data.slug)).first()
    if existing:
        return existing, False
    try:
        with db.begin_nested():
            entity = Entity(
                id=uuid.uuid4(),
                entity_type=EntityType.TECHNOLOGY.value,
                canonical_name=data.name,
                normalized_name=normalize_name(data.name),
                source_system="internal",
                source_key=f"technology:{data.slug}",
            )
            technology = Technology(
                entity=entity,
                slug=data.slug,
                category=data.category,
                description=data.description,
            )
            db.add(technology)
            db.flush()
        return technology, True
    except IntegrityError:
        found = db.scalars(select(Technology).where(Technology.slug == data.slug)).first()
        if found is None:
            raise
        return found, False

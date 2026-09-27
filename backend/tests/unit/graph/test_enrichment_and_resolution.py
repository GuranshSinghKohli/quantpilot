"""Deterministic enrichment and conservative resolution."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import AliasType, EntityType, ResolutionStatus
from app.domain.models import EntityAlias
from app.domain.schemas import CompanyIn
from app.graph.aliases import upsert_alias
from app.graph.normalize import legal_name_stem
from app.graph.resolve import EntityRef, resolve_entity
from app.repositories import entities

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'enrich.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_legal_stem_from_authoritative_name():
    assert legal_name_stem("NVIDIA CORPORATION") == "NVIDIA"
    assert legal_name_stem("Meta Platforms, Inc.") == "Meta Platforms"
    assert legal_name_stem("NVIDIA") is None


def test_cik_source_name_ticker_and_stem_resolve(db):
    company, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810", exchange="NASDAQ")
    )
    by_cik = resolve_entity(db, EntityRef(cik="1045810"))
    by_source = resolve_entity(db, EntityRef(source_system="sec", source_key="0001045810"))
    by_legal = resolve_entity(db, EntityRef(name="NVIDIA Corporation", entity_type=EntityType.COMPANY))
    by_stem = resolve_entity(db, EntityRef(name="NVIDIA", entity_type=EntityType.COMPANY))
    by_ticker = resolve_entity(db, EntityRef(ticker="NVDA", entity_type=EntityType.COMPANY))
    assert {r.status for r in (by_cik, by_source, by_legal, by_stem, by_ticker)} == {ResolutionStatus.RESOLVED}
    assert {r.entity_id for r in (by_cik, by_source, by_legal, by_stem, by_ticker)} == {company.entity_id}
    assert by_cik.matched_by == "cik"
    assert by_source.matched_by == "source_key"
    assert by_ticker.matched_by == "ticker"
    assert by_cik.public_dict()["candidates"] == [str(company.entity_id)]


def test_unresolved_and_ambiguous_are_structured(db):
    missing = resolve_entity(db, EntityRef(name="Nobody Inc"))
    assert missing.status == ResolutionStatus.UNRESOLVED
    assert missing.entity_id is None
    assert missing.candidates == []
    a, _ = entities.get_or_create_company(db, CompanyIn(legal_name="One Corp", ticker="ONE", cik="1"))
    b, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Two Corp", ticker="TWO", cik="2"))
    upsert_alias(db, b.entity_id, "ONE", AliasType.TICKER)
    amb = resolve_entity(db, EntityRef(ticker="ONE", entity_type=EntityType.COMPANY))
    assert amb.status == ResolutionStatus.AMBIGUOUS
    assert set(amb.candidates) == {a.entity_id, b.entity_id}
    assert amb.entity_id is None


def test_ticker_reuse_is_ambiguous_not_identity(db):
    first, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Old NVDA Holder", ticker="HOLD", cik="10"))
    second, _ = entities.get_or_create_company(db, CompanyIn(legal_name="New NVDA Holder", ticker="NVDA", cik="11"))
    upsert_alias(db, first.entity_id, "NVDA", AliasType.TICKER)
    result = resolve_entity(db, EntityRef(ticker="NVDA", entity_type=EntityType.COMPANY))
    assert result.status == ResolutionStatus.AMBIGUOUS
    assert set(result.candidates) == {first.entity_id, second.entity_id}


def test_alias_insert_is_idempotent(db):
    company, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810")
    )
    before = db.query(EntityAlias).filter(EntityAlias.entity_id == company.entity_id).count()
    from app.graph.aliases import enrich_company_aliases

    enrich_company_aliases(db, company)
    after = db.query(EntityAlias).filter(EntityAlias.entity_id == company.entity_id).count()
    assert after == before


def test_repeated_resolution_is_deterministic(db):
    entities.get_or_create_company(db, CompanyIn(legal_name="NVIDIA Corporation", ticker="NVDA", cik="1045810"))
    first = resolve_entity(db, EntityRef(name="NVIDIA", entity_type=EntityType.COMPANY))
    second = resolve_entity(db, EntityRef(name="NVIDIA", entity_type=EntityType.COMPANY))
    assert first.public_dict() == second.public_dict()

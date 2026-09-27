"""Postgres constraints and relationship replay for the graph foundation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import ProvenanceKind, RelationshipType, SourceType
from app.domain.models import Entity, Relationship
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.queries import companies_for_technology, neighbors_postgres, technologies_for_company
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _pair(db):
    a, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Graph Alpha", ticker="GAL", cik="100"))
    b, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Graph Beta", ticker="GBE", cik="101"))
    return a, b


def _ev(db, excerpt="Alpha uses Beta technology.", url="https://example.com/pg-graph"):
    source, _ = sources.upsert_source(
        db, SourceIn(source_type=SourceType.WEB, url=url, retrieved_at=NOW, content_text=excerpt)
    )
    ev, _ = sources.add_evidence(db, source.id, EvidenceIn(excerpt=excerpt, extraction_method="test", extracted_at=NOW))
    return source, ev


def test_source_identity_unique(db):
    entities.get_or_create_company(db, CompanyIn(legal_name="Unique CIK Co", ticker="UCC", cik="202"))
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                insert(Entity).values(
                    id=uuid.uuid4(),
                    entity_type="company",
                    canonical_name="Other Name",
                    normalized_name="other name",
                    source_system="sec",
                    source_key="0000000202",
                )
            )


def test_alias_uniqueness_per_entity(db):
    company, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Alias Co", ticker="ALC", cik="303"))
    from app.domain.enums import AliasType
    from app.graph.aliases import upsert_alias

    assert upsert_alias(db, company.entity_id, "ALC", AliasType.TICKER) is False


def test_self_relationship_rejected(db):
    company, _ = _pair(db)
    source, ev = _ev(db)
    with pytest.raises(ValueError):
        persist_relationship(
            db,
            RelationshipCandidate(
                from_entity=EntityRef(entity_id=company.entity_id),
                relationship_type=RelationshipType.OWNS,
                to_entity=EntityRef(entity_id=company.entity_id),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.5,
            ),
        )


def test_valid_range_constraint(db):
    a, b = _pair(db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                insert(Relationship).values(
                    id=uuid.uuid4(),
                    subject_entity_id=a.entity_id,
                    object_entity_id=b.entity_id,
                    relationship_type="USES",
                    status="asserted",
                    provenance_kind="OBSERVED",
                    first_seen_at=NOW,
                    last_seen_at=NOW,
                    valid_from=NOW,
                    valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc),
                )
            )


def test_replay_and_neighbors(db):
    a, b = _pair(db)
    tech, _ = entities.get_or_create_technology(db, TechnologyIn(name="HBM", slug="hbm"))
    source, ev = _ev(db, "Graph Alpha develops HBM.", url="https://example.com/pg-hbm")
    first, created = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=a.entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=tech.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.9,
            provenance_kind=ProvenanceKind.VERIFIED,
        ),
    )
    second, created_again = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=a.entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=tech.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.9,
            provenance_kind=ProvenanceKind.VERIFIED,
        ),
    )
    assert created and not created_again and first.id == second.id
    assert technologies_for_company(db, a.entity_id)[0]["entity_id"] == str(tech.entity_id)
    assert companies_for_technology(db, tech.entity_id)[0]["entity_id"] == str(a.entity_id)
    near = neighbors_postgres(db, a.entity_id)
    assert {item["entity_id"] for item in near} == {str(tech.entity_id)}
    assert near[0]["relationship"]["evidence_ids"]
    assert near[0]["relationship"]["verified"] is True


def test_weaker_inferred_does_not_overwrite_verified(db):
    a, b = _pair(db)
    source, ev = _ev(db, "Alpha partnered with Beta.", url="https://example.com/pg-partner")
    persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=a.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=b.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.95,
            provenance_kind=ProvenanceKind.VERIFIED,
        ),
    )
    weak, ev2 = _ev(db, "Maybe they partnered.", url="https://example.com/pg-maybe")
    edge, created = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=a.entity_id),
            relationship_type=RelationshipType.PARTNERED_WITH,
            to_entity=EntityRef(entity_id=b.entity_id),
            source_id=weak.id,
            evidence_id=ev2.id,
            confidence=0.1,
            provenance_kind=ProvenanceKind.INFERRED,
        ),
    )
    assert created is False
    assert edge.provenance_kind == ProvenanceKind.VERIFIED.value
    assert edge.confidence == 0.95


def test_concurrent_relationship_insert(pg_engine):
    from sqlalchemy.orm import Session

    with Session(pg_engine) as setup:
        a, _ = entities.get_or_create_company(setup, CompanyIn(legal_name="Conc A", ticker="CNA", cik="401"))
        b, _ = entities.get_or_create_company(setup, CompanyIn(legal_name="Conc B", ticker="CNB", cik="402"))
        source, ev = _ev(setup, "Conc A supplies Conc B.", url="https://example.com/pg-conc")
        setup.commit()
        ids = (a.entity_id, b.entity_id, source.id, ev.id)

    def write():
        with Session(pg_engine) as session:
            edge, _ = persist_relationship(
                session,
                RelationshipCandidate(
                    from_entity=EntityRef(entity_id=ids[0]),
                    relationship_type=RelationshipType.SUPPLIES,
                    to_entity=EntityRef(entity_id=ids[1]),
                    source_id=ids[2],
                    evidence_id=ids[3],
                    confidence=0.7,
                ),
            )
            session.commit()
            return edge.id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            left, right = pool.map(lambda _: write(), range(2))
        assert left == right
        with Session(pg_engine) as check:
            count = check.scalar(text("SELECT count(*) FROM relationships WHERE subject_entity_id = :a"), {"a": ids[0]})
            assert count == 1
    finally:
        with Session(pg_engine) as check:
            check.execute(text("DELETE FROM relationship_evidence WHERE relationship_id IN (SELECT id FROM relationships WHERE subject_entity_id = :a)"), {"a": ids[0]})
            check.execute(text("DELETE FROM relationships WHERE subject_entity_id = :a"), {"a": ids[0]})
            check.execute(text("DELETE FROM graph_projections WHERE record_id IN (:a, :b)"), {"a": ids[0], "b": ids[1]})
            check.execute(text("DELETE FROM entity_aliases WHERE entity_id IN (:a, :b)"), {"a": ids[0], "b": ids[1]})
            check.execute(text("DELETE FROM companies WHERE entity_id IN (:a, :b)"), {"a": ids[0], "b": ids[1]})
            check.execute(text("DELETE FROM evidence_records WHERE id = :e"), {"e": ids[3]})
            check.execute(text("DELETE FROM sources WHERE id = :s"), {"s": ids[2]})
            check.execute(text("DELETE FROM entities WHERE id IN (:a, :b)"), {"a": ids[0], "b": ids[1]})
            check.commit()

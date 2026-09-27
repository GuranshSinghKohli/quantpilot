"""Reconciliation, repair, and quality without writing the developer Neo4j."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import ProvenanceKind, RelationshipType, SourceType
from app.domain.models import Company, Entity, GraphProjectionRun, Relationship
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.errors import GraphUnavailable
from app.graph.quality import graph_quality
from app.graph.reconcile import reconcile, reconcile_summary, repair
from app.graph.resolve import EntityRef
from app.graph.service import RelationshipCandidate, persist_relationship
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'reconcile.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def _seed(db):
    company, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Alpha Co", ticker="ALP", cik="10"))
    tech, _ = entities.get_or_create_technology(db, TechnologyIn(name="Alpha Tech", slug="alpha-tech"))
    source, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.WEB,
            url="https://example.com/alpha-tech",
            retrieved_at=NOW,
            content_text="Alpha Co develops Alpha Tech.",
        ),
    )
    ev, _ = sources.add_evidence(
        db, source.id, EvidenceIn(excerpt="Alpha Co develops Alpha Tech.", extraction_method="test", extracted_at=NOW)
    )
    edge, _ = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=company.entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=tech.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.8,
            provenance_kind=ProvenanceKind.OBSERVED,
        ),
    )
    db.commit()
    return company, tech, edge


def test_matching_state_is_ok(db, monkeypatch):
    from app.graph.projector import _entity_params

    company, tech, edge = _seed(db)
    company_entity = db.get(Entity, company.entity_id)
    tech_entity = db.get(Entity, tech.entity_id)
    monkeypatch.setattr("app.graph.reconcile.probe_neo4j", lambda: "ok")
    monkeypatch.setattr(
        "app.graph.reconcile._neo4j_entities",
        lambda: {
            str(company.entity_id): _entity_params(company_entity),
            str(tech.entity_id): _entity_params(tech_entity),
        },
    )
    monkeypatch.setattr(
        "app.graph.reconcile._neo4j_relationships",
        lambda: {
            str(edge.id): {
                "relationship_id": str(edge.id),
                "relationship_type": edge.relationship_type,
                "from_entity_id": str(edge.subject_entity_id),
                "to_entity_id": str(edge.object_entity_id),
                "confidence": edge.confidence,
                "provenance_kind": edge.provenance_kind,
                "status": edge.status,
            }
        },
    )
    report = reconcile(db)
    summary = reconcile_summary(report)
    assert summary["status"] == "OK"
    assert summary["missing_entities"] == 0
    assert summary["extra_entities"] == 0
    assert summary["missing_relationships"] == 0
    assert summary["extra_relationships"] == 0
    assert summary["property_mismatches"] == 0


def test_missing_extra_and_mismatch(db, monkeypatch):
    company, tech, edge = _seed(db)
    extra_id = "00000000-0000-0000-0000-000000000099"
    monkeypatch.setattr("app.graph.reconcile.probe_neo4j", lambda: "ok")
    monkeypatch.setattr(
        "app.graph.reconcile._neo4j_entities",
        lambda: {
            str(company.entity_id): {
                "entity_id": str(company.entity_id),
                "entity_type": "company",
                "canonical_name": "WRONG",
                "normalized_name": "wrong",
                "source_system": "sec",
                "source_key": "0000000010",
                "cik": "0000000010",
                "tickers": ["ALP"],
                "legal_name": "Alpha Co",
                "exchange": None,
            },
            extra_id: {"entity_id": extra_id, "canonical_name": "Ghost"},
        },
    )
    monkeypatch.setattr("app.graph.reconcile._neo4j_relationships", lambda: {})
    report = reconcile(db)
    assert str(tech.entity_id) in report["missing_entities"]
    assert extra_id in report["extra_entities"]
    assert str(edge.id) in report["missing_relationships"]
    assert report["property_mismatches"]
    assert report["status"] == "DRIFT"


def test_neo4j_unavailable_does_not_change_postgres(db, monkeypatch):
    company, _, _ = _seed(db)
    before = db.query(Company).count()
    monkeypatch.setattr("app.graph.reconcile.probe_neo4j", lambda: "unavailable")
    report = reconcile(db)
    assert report["status"] == "UNAVAILABLE"
    with pytest.raises(GraphUnavailable):
        repair(db)
    assert db.query(Company).count() == before
    assert db.query(Relationship).count() == 1


def test_repair_upserts_without_deleting(db, monkeypatch):
    company, tech, edge = _seed(db)
    calls = []

    def fake_upsert_entity(entity):
        calls.append(("entity", str(entity.id)))

    def fake_upsert_rel(rel):
        calls.append(("rel", str(rel.id)))

    monkeypatch.setattr("app.graph.reconcile.probe_neo4j", lambda: "ok")
    monkeypatch.setattr("app.graph.reconcile.ensure_schema", lambda: None)
    monkeypatch.setattr(
        "app.graph.reconcile.reconcile",
        lambda _db: {
            "missing_entities": [str(company.entity_id)],
            "missing_relationships": [str(edge.id)],
            "property_mismatches": [{"entity_id": str(tech.entity_id), "fields": ["canonical_name"]}],
            "extra_entities": ["00000000-0000-0000-0000-000000000099"],
            "extra_relationships": [],
            "projection_failures": 0,
            "status": "DRIFT",
            "entities_postgres": 2,
            "relationships_postgres": 1,
            "entities_neo4j": 1,
            "relationships_neo4j": 0,
            "neo4j": "ok",
        },
    )
    monkeypatch.setattr("app.graph.reconcile.upsert_entity_node", fake_upsert_entity)
    monkeypatch.setattr("app.graph.reconcile.upsert_relationship_edge", fake_upsert_rel)
    result = repair(db, allow_delete=False)
    assert result["ok"]
    assert ("entity", str(company.entity_id)) in calls
    assert ("rel", str(edge.id)) in calls
    assert result["deleted"] == 0
    assert db.query(GraphProjectionRun).count() == 1
    assert db.query(Company).count() == 1


def test_quality_is_read_only(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr("app.graph.quality.probe_neo4j", lambda: "unconfigured")
    report = graph_quality(db)
    assert report["entities"]["total"] == 2
    assert report["relationships"]["total"] == 1
    assert report["relationships"]["missing_evidence"] == 0
    assert report["candidates"]["pending"] == 0
    assert db.query(Relationship).count() == 1

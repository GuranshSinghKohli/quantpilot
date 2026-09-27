"""Reconciliation and opt-in repair against the isolated Neo4j instance."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import ProvenanceKind, RelationshipType, SourceType
from app.domain.models import Company, Relationship
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.projector import project
from app.graph.reconcile import reconcile, reconcile_summary, repair
from app.graph.resolve import EntityRef
from app.graph.schema import ensure_schema
from app.graph.service import RelationshipCandidate, persist_relationship
from app.graph.store import run_cypher
from app.repositories import entities, sources
from tests.neo4j.conftest import graph_counts

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'neo4j-repair.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    return engine, Session()


def _seed(db):
    company, _ = entities.get_or_create_company(db, CompanyIn(legal_name="Repair Co", ticker="REP", cik="808"))
    tech, _ = entities.get_or_create_technology(db, TechnologyIn(name="Repair Tech", slug="repair-tech"))
    source, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.WEB,
            url="https://example.com/repair-tech",
            retrieved_at=NOW,
            content_text="Repair Co develops Repair Tech.",
        ),
    )
    ev, _ = sources.add_evidence(
        db,
        source.id,
        EvidenceIn(excerpt="Repair Co develops Repair Tech.", extraction_method="test", extracted_at=NOW),
    )
    edge, _ = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=company.entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=tech.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.7,
            provenance_kind=ProvenanceKind.OBSERVED,
        ),
    )
    db.commit()
    return company, tech, edge


def test_reconcile_matching_and_repair_missing(tmp_path, neo4j_settings):
    engine, db = _db(tmp_path)
    try:
        company, tech, edge = _seed(db)
        ensure_schema()
        project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
        summary = reconcile_summary(reconcile(db))
        assert summary["status"] == "OK"
        assert summary["entities_postgres"] == 2
        assert summary["entities_neo4j"] == 2
        assert summary["relationships_postgres"] == 1
        assert summary["relationships_neo4j"] == 1

        run_cypher("MATCH (e:Entity {entity_id: $id}) DETACH DELETE e", {"id": str(tech.entity_id)})
        drifted = reconcile(db)
        assert str(tech.entity_id) in drifted["missing_entities"]
        assert drifted["status"] == "DRIFT"

        result = repair(db)
        assert result["ok"]
        after = reconcile_summary(reconcile(db))
        assert after["status"] == "OK"
        assert after["missing_entities"] == 0
        assert after["missing_relationships"] == 0
    finally:
        db.close()
        engine.dispose()


def test_repair_updates_mismatched_properties(tmp_path, neo4j_settings):
    engine, db = _db(tmp_path)
    try:
        company, tech, edge = _seed(db)
        project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
        run_cypher(
            "MATCH (e:Entity {entity_id: $id}) SET e.canonical_name = $name",
            {"id": str(company.entity_id), "name": "Stale Name"},
        )
        drifted = reconcile(db)
        assert any(item.get("entity_id") == str(company.entity_id) for item in drifted["property_mismatches"])
        repair(db)
        name = run_cypher(
            "MATCH (e:Entity {entity_id: $id}) RETURN e.canonical_name AS name",
            {"id": str(company.entity_id)},
        )[0]["name"]
        assert name == "Repair Co"
        assert db.get(Company, company.entity_id).legal_name == "Repair Co"
    finally:
        db.close()
        engine.dispose()


def test_extra_neo4j_node_is_reported_not_deleted(tmp_path, neo4j_settings):
    engine, db = _db(tmp_path)
    try:
        company, tech, edge = _seed(db)
        project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
        run_cypher("CREATE (:Entity {entity_id: $id, canonical_name: $name})", {"id": "extra-node", "name": "Extra"})
        report = reconcile(db)
        assert "extra-node" in report["extra_entities"]
        repair(db, allow_delete=False)
        leftover = run_cypher("MATCH (e:Entity {entity_id: $id}) RETURN count(e) AS n", {"id": "extra-node"})[0]["n"]
        assert leftover == 1
        assert db.query(Relationship).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_rejected_candidate_never_reaches_neo4j(tmp_path, neo4j_settings):
    from app.graph.candidates import CandidateProposal, RelationshipCandidateService

    engine, db = _db(tmp_path)
    try:
        company, tech, _edge = _seed(db)
        source, _ = sources.upsert_source(
            db,
            SourceIn(
                source_type=SourceType.WEB,
                url="https://example.com/reject-neo",
                retrieved_at=NOW,
                content_text="Repair Co uses Repair Tech in production.",
            ),
        )
        ev, _ = sources.add_evidence(
            db,
            source.id,
            EvidenceIn(excerpt="Repair Co uses Repair Tech in production.", extraction_method="test", extracted_at=NOW),
        )
        svc = RelationshipCandidateService()
        row, _ = svc.propose(
            db,
            CandidateProposal(
                from_entity=EntityRef(entity_id=company.entity_id),
                relationship_type=RelationshipType.USES,
                to_entity=EntityRef(entity_id=tech.entity_id),
                source_id=source.id,
                evidence_id=ev.id,
                confidence=0.4,
            ),
        )
        svc.reject(db, row.id)
        db.commit()
        project(db)
        uses = run_cypher("MATCH ()-[r:USES]->() RETURN count(r) AS n")[0]["n"]
        assert uses == 0
        nodes, rels = graph_counts()
        assert nodes == 2 and rels == 1
    finally:
        db.close()
        engine.dispose()

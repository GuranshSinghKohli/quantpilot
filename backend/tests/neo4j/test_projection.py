"""Neo4j schema, idempotent projection, bounded queries, failure isolation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import ProvenanceKind, RelationshipType, SourceType
from app.domain.schemas import CompanyIn, EvidenceIn, SourceIn, TechnologyIn
from app.graph.projector import project
from app.graph.queries import neighbors
from app.graph.resolve import EntityRef
from app.graph.schema import ensure_schema, schema_status
from app.graph.service import RelationshipCandidate, persist_relationship
from app.graph.store import close_driver, probe_neo4j, run_cypher
from app.repositories import entities, sources

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path: Path, neo4j_settings):
    engine = build_engine(f"sqlite:///{tmp_path / 'neo4j-graph.db'}")
    upgrade_to_head(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(db):
    company, _ = entities.get_or_create_company(
        db, CompanyIn(legal_name="Neo Company", ticker="NEO", cik="909")
    )
    tech, _ = entities.get_or_create_technology(db, TechnologyIn(name="Neo Tech", slug="neo-tech"))
    source, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.WEB,
            url="https://example.com/neo-tech",
            retrieved_at=NOW,
            content_text="Neo Company develops Neo Tech.",
        ),
    )
    ev, _ = sources.add_evidence(
        db,
        source.id,
        EvidenceIn(excerpt="Neo Company develops Neo Tech.", extraction_method="test", extracted_at=NOW),
    )
    edge, _ = persist_relationship(
        db,
        RelationshipCandidate(
            from_entity=EntityRef(entity_id=company.entity_id),
            relationship_type=RelationshipType.DEVELOPS,
            to_entity=EntityRef(entity_id=tech.entity_id),
            source_id=source.id,
            evidence_id=ev.id,
            confidence=0.88,
            provenance_kind=ProvenanceKind.OBSERVED,
        ),
    )
    db.commit()
    return company, tech, edge


def test_schema_and_repeated_projection(db, neo4j_settings):
    assert probe_neo4j() == "ok"
    ensure_schema()
    status = schema_status()
    assert status["ok"] is True
    company, tech, edge = _seed(db)
    first = project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
    second = project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
    assert first["ok"] and second["ok"]
    nodes = run_cypher(
        "MATCH (e:Entity) WHERE e.entity_id IN $ids RETURN count(e) AS n",
        {"ids": [str(company.entity_id), str(tech.entity_id)]},
    )[0]["n"]
    rels = run_cypher(
        "MATCH ()-[r:DEVELOPS {relationship_id: $rid}]->() RETURN count(r) AS n",
        {"rid": str(edge.id)},
    )[0]["n"]
    assert nodes == 2 and rels == 1
    props = run_cypher(
        "MATCH ()-[r:DEVELOPS {relationship_id: $rid}]->() "
        "RETURN r.evidence_id AS evidence_id, r.source_id AS source_id, r.confidence AS confidence",
        {"rid": str(edge.id)},
    )[0]
    assert props["evidence_id"] and props["source_id"] and props["confidence"] == 0.88
    near = neighbors(db, company.entity_id, depth=1)
    assert str(tech.entity_id) in {item["entity_id"] for item in near}


def test_bounded_traversal_and_parameterized_lookup(db, neo4j_settings):
    company, tech, edge = _seed(db)
    project(db, entity_ids=[company.entity_id, tech.entity_id], relationship_ids=[edge.id])
    rows = run_cypher(
        "MATCH (e:Entity {entity_id: $entity_id})-[:DEVELOPS]->(t:Entity) "
        "RETURN t.entity_id AS entity_id, t.canonical_name AS name",
        {"entity_id": str(company.entity_id)},
    )
    assert rows[0]["entity_id"] == str(tech.entity_id)
    deep = neighbors(db, company.entity_id, depth=2)
    assert any(row.get("entity_id") == str(tech.entity_id) for row in deep)


def test_projection_failure_does_not_corrupt_postgres(db, neo4j_settings, monkeypatch):
    company, tech, edge = _seed(db)
    from app.graph import projector

    def boom(*_a, **_k):
        raise RuntimeError("neo4j exploded")

    monkeypatch.setattr(projector, "upsert_entity_node", boom)
    result = project(db, entity_ids=[company.entity_id], relationship_ids=[], init_schema=False)
    assert result["failures"] >= 1
    refreshed = db.get(type(company), company.entity_id)
    assert refreshed is not None
    assert refreshed.legal_name == "Neo Company"


def test_cypher_is_not_built_from_request_strings():
    from app.graph import queries

    source = Path(queries.__file__).read_text(encoding="utf-8")
    assert "[*1..$depth]" not in source
    assert '{1: "[*1..1]", 2: "[*1..2]", 3: "[*1..3]"}' in source

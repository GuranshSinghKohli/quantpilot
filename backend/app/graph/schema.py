"""Neo4j constraints and indexes. Idempotent. Documented in GRAPH_SCHEMA.md."""

from __future__ import annotations

from typing import List

from app.domain.enums import RelationshipType
from app.graph.store import run_cypher

ENTITY_CONSTRAINTS = (
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE INDEX entity_canonical_name IF NOT EXISTS FOR (e:Entity) ON (e.canonical_name)",
    "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.entity_type)",
    "CREATE INDEX company_cik IF NOT EXISTS FOR (c:Company) ON (c.cik)",
    "CREATE CONSTRAINT canonical_event_id IF NOT EXISTS FOR (e:CanonicalEvent) REQUIRE e.event_id IS UNIQUE",
)


def _rel_constraint(rel_type: str) -> str:
    return (
        f"CREATE CONSTRAINT rel_{rel_type.lower()}_id IF NOT EXISTS "
        f"FOR ()-[r:{rel_type}]-() REQUIRE r.relationship_id IS UNIQUE"
    )


def schema_statements() -> List[str]:
    return (
        list(ENTITY_CONSTRAINTS)
        + [_rel_constraint(t.value) for t in RelationshipType]
        + [_rel_constraint("INVOLVES")]
    )


def ensure_schema() -> List[str]:
    applied = []
    for statement in schema_statements():
        run_cypher(statement)
        applied.append(statement)
    return applied


def schema_status() -> dict:
    try:
        constraints = run_cypher("SHOW CONSTRAINTS YIELD name RETURN name")
        indexes = run_cypher("SHOW INDEXES YIELD name RETURN name")
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
    names = {row["name"] for row in constraints + indexes}
    expected = {"entity_id", "entity_canonical_name", "entity_type", "company_cik", "canonical_event_id"}
    expected.update(f"rel_{t.value.lower()}_id" for t in RelationshipType)
    expected.add("rel_involves_id")
    return {
        "ok": expected.issubset(names),
        "present": sorted(names & expected),
        "missing": sorted(expected - names),
    }

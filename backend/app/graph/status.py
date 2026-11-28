"""Operational graph status. Never includes credentials."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import GraphProjection
from app.graph.projector import pending_counts
from app.graph.schema import schema_status
from app.graph.store import neo4j_configured, probe_neo4j, run_cypher


def graph_status(db: Session) -> dict:
    pending = pending_counts(db)
    last = db.scalars(
        select(GraphProjection).where(GraphProjection.status == "success").order_by(GraphProjection.projected_at.desc())
    ).first()
    neo4j = probe_neo4j()
    nodes = rels = None
    schema = {"ok": False, "present": [], "missing": ["unconfigured"]}
    if neo4j == "ok":
        try:
            nodes = run_cypher("MATCH (n:Entity) RETURN count(n) AS n")[0]["n"]
            rels = run_cypher("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
            schema = schema_status()
        except Exception:
            neo4j = "unavailable"
    return {
        "postgres_entities": pending["entities"],
        "postgres_relationships": pending["relationships"],
        "neo4j_nodes": nodes,
        "neo4j_relationships": rels,
        "neo4j": neo4j,
        "neo4j_configured": neo4j_configured(),
        "last_successful_projection": last.projected_at.isoformat() if last else None,
        "pending_projection_count": pending["pending"],
        "projection_failures": pending["failed"],
        "schema": schema,
    }


def graph_check(db: Session) -> dict:
    status = graph_status(db)
    problems = []
    if status["neo4j"] == "ok":
        if status["neo4j_nodes"] is not None and status["neo4j_nodes"] < status["postgres_entities"]:
            problems.append("neo4j node count is behind postgres entities")
        if status["neo4j_relationships"] is not None and status["neo4j_relationships"] < status["postgres_relationships"]:
            problems.append("neo4j relationship count is behind postgres")
        if status["schema"] and not status["schema"].get("ok"):
            problems.append("neo4j schema is incomplete")
    if status["projection_failures"]:
        problems.append(f"{status['projection_failures']} projection failures")
    if status["pending_projection_count"]:
        problems.append(f"{status['pending_projection_count']} records pending projection")
    if status["neo4j"] != "ok":
        status["ok"] = True
        status["note"] = "Neo4j is not required; Postgres remains authoritative"
    else:
        status["ok"] = not problems
    status["problems"] = problems
    return status

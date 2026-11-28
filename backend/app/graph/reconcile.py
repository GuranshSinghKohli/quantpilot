"""Read-only Postgres ↔ Neo4j reconciliation. Repair is a separate opt-in."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.enums import EventStatus
from app.domain.models import Entity, Event, EventEntity, GraphProjection, GraphProjectionRun, Relationship
from app.graph.errors import GraphUnavailable
from app.graph.metrics import GRAPH_METRICS
from app.graph.projector import (
    _entity_params,
    _event_link_record_id,
    _record,
    event_link_id,
    upsert_entity_node,
    upsert_event_link,
    upsert_event_node,
    upsert_relationship_edge,
)
from app.graph.schema import ensure_schema
from app.graph.store import probe_neo4j, run_cypher
from app.observability.logger import get_logger, log_event

logger = get_logger("graph")

_COMPARE_KEYS = (
    "entity_type",
    "canonical_name",
    "normalized_name",
    "source_system",
    "source_key",
    "cik",
    "tickers",
    "legal_name",
    "exchange",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _neo4j_entities() -> Dict[str, Dict[str, Any]]:
    rows = run_cypher(
        "MATCH (e:Entity) RETURN e.entity_id AS entity_id, e.entity_type AS entity_type, "
        "e.canonical_name AS canonical_name, e.normalized_name AS normalized_name, "
        "e.source_system AS source_system, e.source_key AS source_key, e.cik AS cik, "
        "e.tickers AS tickers, e.legal_name AS legal_name, e.exchange AS exchange"
    )
    return {str(row["entity_id"]): row for row in rows if row.get("entity_id")}


def _neo4j_relationships() -> Dict[str, Dict[str, Any]]:
    rows = run_cypher(
        "MATCH (a:Entity)-[r]->(b:Entity) "
        "RETURN r.relationship_id AS relationship_id, type(r) AS relationship_type, "
        "a.entity_id AS from_entity_id, b.entity_id AS to_entity_id, "
        "r.confidence AS confidence, r.provenance_kind AS provenance_kind, r.status AS status"
    )
    return {str(row["relationship_id"]): row for row in rows if row.get("relationship_id")}


def _neo4j_events() -> Dict[str, Dict[str, Any]]:
    rows = run_cypher(
        "MATCH (e:CanonicalEvent) RETURN e.event_id AS event_id, e.event_type AS event_type, "
        "e.status AS status, e.title AS title"
    )
    return {str(row["event_id"]): row for row in rows if row.get("event_id")}


def _neo4j_event_links() -> Dict[str, Dict[str, Any]]:
    rows = run_cypher(
        "MATCH (e:CanonicalEvent)-[r:INVOLVES]->(n:Entity) "
        "RETURN r.link_id AS link_id, e.event_id AS event_id, n.entity_id AS entity_id, r.role AS role"
    )
    return {str(row["link_id"]): row for row in rows if row.get("link_id")}


def _mismatch(expected: Dict[str, Any], actual: Dict[str, Any], keys: Sequence[str]) -> List[str]:
    found = []
    for key in keys:
        left, right = expected.get(key), actual.get(key)
        if isinstance(left, list) or isinstance(right, list):
            if list(left or []) != list(right or []):
                found.append(key)
        elif left != right:
            found.append(key)
    return found


def reconcile(db: Session) -> dict:
    """Compare Postgres authoritative IDs to Neo4j. Never mutates either store."""
    started = time.perf_counter()
    entities = list(db.scalars(select(Entity).options(selectinload(Entity.company))).unique())
    edges = list(db.scalars(select(Relationship)).unique())
    events = list(db.scalars(select(Event).where(Event.status == EventStatus.CONFIRMED.value)))
    event_links = list(
        db.scalars(
            select(EventEntity)
            .join(Event, Event.id == EventEntity.event_id)
            .where(Event.status == EventStatus.CONFIRMED.value)
        )
    )
    failed = db.scalars(select(GraphProjection).where(GraphProjection.status == "failed")).all()

    neo4j = probe_neo4j()
    report: Dict[str, Any] = {
        "entities_postgres": len(entities),
        "relationships_postgres": len(edges),
        "events_postgres": len(events),
        "event_links_postgres": len(event_links),
        "entities_neo4j": None,
        "relationships_neo4j": None,
        "events_neo4j": None,
        "event_links_neo4j": None,
        "missing_entities": [],
        "extra_entities": [],
        "missing_relationships": [],
        "extra_relationships": [],
        "missing_events": [],
        "extra_events": [],
        "missing_event_relationships": [],
        "extra_event_relationships": [],
        "property_mismatches": [],
        "projection_failures": len(failed),
        "neo4j": neo4j,
        "status": "UNAVAILABLE" if neo4j != "ok" else "OK",
    }
    if neo4j != "ok":
        GRAPH_METRICS.inc("graph_reconciliation_total", operation="reconcile", status="unavailable")
        if neo4j == "unavailable":
            GRAPH_METRICS.inc("graph_reconciliation_failures_total", operation="reconcile", status="unavailable")
        duration = time.perf_counter() - started
        log_event(
            logger,
            20,
            "GRAPH_RECONCILED",
            component="graph",
            operation="reconcile",
            status=report["status"],
            duration_ms=int(duration * 1000),
        )
        return report

    try:
        nodes = _neo4j_entities()
        rels = _neo4j_relationships()
        event_nodes = _neo4j_events()
        event_rels = _neo4j_event_links()
    except GraphUnavailable:
        report["neo4j"] = "unavailable"
        report["status"] = "UNAVAILABLE"
        GRAPH_METRICS.inc("graph_reconciliation_total", operation="reconcile", status="unavailable")
        GRAPH_METRICS.inc("graph_reconciliation_failures_total", operation="reconcile", status="unavailable")
        return report

    pg_entity_ids = {str(entity.id) for entity in entities}
    pg_rel_ids = {str(edge.id) for edge in edges}
    pg_event_ids = {str(event.id) for event in events}
    pg_event_link_ids = {event_link_id(link.event_id, link.entity_id, link.role) for link in event_links}
    report["entities_neo4j"] = len(nodes)
    report["relationships_neo4j"] = len(rels)
    report["events_neo4j"] = len(event_nodes)
    report["event_links_neo4j"] = len(event_rels)
    report["missing_entities"] = sorted(pg_entity_ids - set(nodes))
    report["extra_entities"] = sorted(set(nodes) - pg_entity_ids)
    report["missing_relationships"] = sorted(pg_rel_ids - set(rels))
    report["extra_relationships"] = sorted(set(rels) - pg_rel_ids)
    report["missing_events"] = sorted(pg_event_ids - set(event_nodes))
    report["extra_events"] = sorted(set(event_nodes) - pg_event_ids)
    report["missing_event_relationships"] = sorted(pg_event_link_ids - set(event_rels))
    report["extra_event_relationships"] = sorted(set(event_rels) - pg_event_link_ids)

    by_id = {str(entity.id): entity for entity in entities}
    for entity_id, node in nodes.items():
        entity = by_id.get(entity_id)
        if entity is None:
            continue
        expected = _entity_params(entity)
        keys = _mismatch(expected, node, _COMPARE_KEYS)
        if keys:
            report["property_mismatches"].append({"entity_id": entity_id, "fields": keys})

    by_rel = {str(edge.id): edge for edge in edges}
    for rel_id, rel in rels.items():
        edge = by_rel.get(rel_id)
        if edge is None:
            continue
        expected = {
            "relationship_type": edge.relationship_type,
            "from_entity_id": str(edge.subject_entity_id),
            "to_entity_id": str(edge.object_entity_id),
            "confidence": edge.confidence,
            "provenance_kind": edge.provenance_kind,
            "status": edge.status,
        }
        keys = _mismatch(expected, rel, expected.keys())
        if keys:
            report["property_mismatches"].append({"relationship_id": rel_id, "fields": keys})

    dirty = (
        report["missing_entities"]
        or report["extra_entities"]
        or report["missing_relationships"]
        or report["extra_relationships"]
        or report["missing_events"]
        or report["extra_events"]
        or report["missing_event_relationships"]
        or report["extra_event_relationships"]
        or report["property_mismatches"]
        or report["projection_failures"]
    )
    report["status"] = "DRIFT" if dirty else "OK"
    report["missing_entity_count"] = len(report["missing_entities"])
    report["extra_entity_count"] = len(report["extra_entities"])
    report["missing_relationship_count"] = len(report["missing_relationships"])
    report["extra_relationship_count"] = len(report["extra_relationships"])
    report["missing_event_count"] = len(report["missing_events"])
    report["extra_event_count"] = len(report["extra_events"])
    report["missing_event_relationship_count"] = len(report["missing_event_relationships"])
    report["extra_event_relationship_count"] = len(report["extra_event_relationships"])
    report["property_mismatch_count"] = len(report["property_mismatches"])

    GRAPH_METRICS.inc("graph_reconciliation_total", operation="reconcile", status=report["status"].lower())
    GRAPH_METRICS.inc(
        "graph_entities_reconciled_total",
        operation="reconcile",
        status=report["status"].lower(),
        result="ok" if not report["missing_entities"] and not report["extra_entities"] else "drift",
    )
    GRAPH_METRICS.inc(
        "graph_relationships_reconciled_total",
        operation="reconcile",
        status=report["status"].lower(),
        result="ok" if not report["missing_relationships"] and not report["extra_relationships"] else "drift",
    )
    duration = time.perf_counter() - started
    log_event(
        logger,
        20,
        "GRAPH_RECONCILED",
        component="graph",
        operation="reconcile",
        status=report["status"],
        duration_ms=int(duration * 1000),
    )
    return report


def reconcile_summary(report: dict) -> dict:
    return {
        "entities_postgres": report["entities_postgres"],
        "entities_neo4j": report["entities_neo4j"],
        "missing_entities": report.get("missing_entity_count", len(report.get("missing_entities") or [])),
        "extra_entities": report.get("extra_entity_count", len(report.get("extra_entities") or [])),
        "relationships_postgres": report["relationships_postgres"],
        "relationships_neo4j": report["relationships_neo4j"],
        "missing_relationships": report.get(
            "missing_relationship_count", len(report.get("missing_relationships") or [])
        ),
        "extra_relationships": report.get(
            "extra_relationship_count", len(report.get("extra_relationships") or [])
        ),
        "property_mismatches": report.get(
            "property_mismatch_count", len(report.get("property_mismatches") or [])
        ),
        "events_postgres": report.get("events_postgres", 0),
        "events_neo4j": report.get("events_neo4j"),
        "missing_events": report.get("missing_event_count", len(report.get("missing_events") or [])),
        "extra_events": report.get("extra_event_count", len(report.get("extra_events") or [])),
        "event_links_postgres": report.get("event_links_postgres", 0),
        "event_links_neo4j": report.get("event_links_neo4j"),
        "missing_event_relationships": report.get(
            "missing_event_relationship_count", len(report.get("missing_event_relationships") or [])
        ),
        "extra_event_relationships": report.get(
            "extra_event_relationship_count", len(report.get("extra_event_relationships") or [])
        ),
        "projection_failures": report.get("projection_failures", 0),
        "status": report["status"],
        "neo4j": report.get("neo4j"),
    }


def _checkpoint(
    db: Session,
    *,
    operation: str,
    status: str,
    entities: int,
    relationships: int,
    failures: int,
    started_at: datetime,
    error: Optional[str] = None,
    source: str = "cli",
) -> None:
    db.add(
        GraphProjectionRun(
            operation=operation,
            status=status,
            entities_projected=entities,
            relationships_projected=relationships,
            events_projected=0,
            event_links_projected=0,
            failures=failures,
            source=source,
            error=error,
            started_at=started_at,
            finished_at=_now(),
        )
    )


def repair(
    db: Session,
    *,
    allow_delete: bool = False,
    confirm_delete: bool = False,
    init_schema: bool = True,
    source: str = "cli",
) -> dict:
    """Opt-in repair. Default: upsert missing + refresh mismatched properties.

    Extra Neo4j rows are never deleted unless both ``allow_delete`` and
    ``confirm_delete`` are true. Repair never writes canonical Postgres rows.
    """
    started_at = _now()
    started = time.perf_counter()
    if probe_neo4j() != "ok":
        GRAPH_METRICS.inc("graph_repair_total", operation="repair", status="unavailable")
        GRAPH_METRICS.inc("graph_reconciliation_failures_total", operation="repair", status="unavailable")
        raise GraphUnavailable("Neo4j is unavailable; Postgres was not changed")

    report = reconcile(db)
    if init_schema:
        ensure_schema()

    entities = {str(e.id): e for e in db.scalars(select(Entity).options(selectinload(Entity.company))).unique()}
    edges = {str(r.id): r for r in db.scalars(select(Relationship).options(selectinload(Relationship.evidence_links))).unique()}
    events = {
        str(e.id): e
        for e in db.scalars(select(Event).where(Event.status == EventStatus.CONFIRMED.value))
    }
    links = {
        event_link_id(link.event_id, link.entity_id, link.role): link
        for link in db.scalars(
            select(EventEntity)
            .join(Event, Event.id == EventEntity.event_id)
            .where(Event.status == EventStatus.CONFIRMED.value)
        )
    }

    repaired_e = repaired_r = updated = deleted = failures = 0
    try:
        for entity_id in report.get("missing_entities") or []:
            entity = entities.get(entity_id)
            if entity is None:
                continue
            try:
                upsert_entity_node(entity)
                _record(db, "entity", entity.id, "success")
                repaired_e += 1
            except Exception as exc:
                failures += 1
                _record(db, "entity", entity.id, "failed", type(exc).__name__)

        for rel_id in report.get("missing_relationships") or []:
            edge = edges.get(rel_id)
            if edge is None:
                continue
            try:
                upsert_relationship_edge(edge)
                _record(db, "relationship", edge.id, "success")
                repaired_r += 1
            except Exception as exc:
                failures += 1
                _record(db, "relationship", edge.id, "failed", type(exc).__name__)

        for event_id in report.get("missing_events") or []:
            event = events.get(event_id)
            if event is None:
                continue
            try:
                upsert_event_node(event)
                _record(db, "event", event.id, "success")
                repaired_e += 1
            except Exception as exc:
                failures += 1
                _record(db, "event", event.id, "failed", type(exc).__name__)

        for link_id in report.get("missing_event_relationships") or []:
            link = links.get(link_id)
            if link is None:
                continue
            try:
                upsert_event_link(link)
                _record(db, "event_link", _event_link_record_id(link), "success")
                repaired_r += 1
            except Exception as exc:
                failures += 1
                _record(db, "event_link", _event_link_record_id(link), "failed", type(exc).__name__)

        for mismatch in report["property_mismatches"]:
            if "entity_id" in mismatch:
                entity = entities.get(mismatch["entity_id"])
                if entity is None:
                    continue
                try:
                    upsert_entity_node(entity)
                    _record(db, "entity", entity.id, "success")
                    updated += 1
                except Exception as exc:
                    failures += 1
                    _record(db, "entity", entity.id, "failed", type(exc).__name__)
            elif "relationship_id" in mismatch:
                edge = edges.get(mismatch["relationship_id"])
                if edge is None:
                    continue
                try:
                    upsert_relationship_edge(edge)
                    _record(db, "relationship", edge.id, "success")
                    updated += 1
                except Exception as exc:
                    failures += 1
                    _record(db, "relationship", edge.id, "failed", type(exc).__name__)

        if allow_delete and confirm_delete:
            extras: Set[str] = set(report.get("extra_entities") or [])
            extra_rels: Set[str] = set(report.get("extra_relationships") or [])
            extra_events: Set[str] = set(report.get("extra_events") or [])
            extra_event_rels: Set[str] = set(report.get("extra_event_relationships") or [])
            for entity_id in extras:
                run_cypher("MATCH (e:Entity {entity_id: $entity_id}) DETACH DELETE e", {"entity_id": entity_id})
                deleted += 1
            for rel_id in extra_rels:
                run_cypher("MATCH ()-[r {relationship_id: $rid}]->() DELETE r", {"rid": rel_id})
                deleted += 1
            for event_id in extra_events:
                run_cypher("MATCH (e:CanonicalEvent {event_id: $event_id}) DETACH DELETE e", {"event_id": event_id})
                deleted += 1
            for link_id in extra_event_rels:
                run_cypher("MATCH ()-[r:INVOLVES {link_id: $link_id}]->() DELETE r", {"link_id": link_id})
                deleted += 1
        elif allow_delete and not confirm_delete:
            raise ValueError("--allow-delete requires --confirm-delete")

        status = "success" if failures == 0 else "partial"
        _checkpoint(
            db,
            operation="repair",
            status=status,
            entities=repaired_e + updated,
            relationships=repaired_r,
            failures=failures,
            started_at=started_at,
            source=source,
        )
        db.commit()
    except GraphUnavailable:
        db.rollback()
        GRAPH_METRICS.inc("graph_repair_total", operation="repair", status="unavailable")
        raise

    GRAPH_METRICS.inc("graph_repair_total", operation="repair", status=status)
    duration = time.perf_counter() - started
    log_event(
        logger,
        20,
        "GRAPH_REPAIRED",
        component="graph",
        operation="repair",
        status=status,
        duration_ms=int(duration * 1000),
    )
    return {
        "ok": failures == 0,
        "entities_upserted": repaired_e,
        "relationships_upserted": repaired_r,
        "properties_updated": updated,
        "deleted": deleted,
        "failures": failures,
        "allow_delete": allow_delete and confirm_delete,
        "duration_seconds": round(duration, 3),
    }

"""Idempotent Postgres → Neo4j projection. MERGE on stable ids only."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.pipeline_context import pipeline_bind
from app.domain.enums import EntityType, EventStatus
from app.domain.models import (
    Entity,
    Event,
    EventEntity,
    GraphProjection,
    GraphProjectionRun,
    Relationship,
    RelationshipEvidence,
)
from app.graph.errors import GraphUnavailable
from app.graph.metrics import GRAPH_METRICS
from app.graph.schema import ensure_schema
from app.graph.store import run_cypher
from app.observability.logger import get_logger, log_event

logger = get_logger("graph")

_LABEL = {
    EntityType.COMPANY.value: "Company",
    EntityType.TECHNOLOGY.value: "Technology",
    EntityType.PRODUCT.value: "Product",
    EntityType.PERSON.value: "Person",
    EntityType.ORGANIZATION.value: "Organization",
    EntityType.EVENT.value: "Event",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record(db: Session, kind: str, record_id: uuid.UUID, status: str, error: Optional[str] = None) -> None:
    row = db.scalars(
        select(GraphProjection).where(
            GraphProjection.record_kind == kind,
            GraphProjection.record_id == record_id,
        )
    ).first()
    if row is None:
        db.add(
            GraphProjection(
                record_kind=kind,
                record_id=record_id,
                projected_id=str(record_id),
                status=status,
                error=error,
                projected_at=_now(),
            )
        )
    else:
        row.status = status
        row.error = error
        row.projected_at = _now()
        row.projected_id = str(record_id)
    db.flush()


def _entity_params(entity: Entity) -> dict:
    company = entity.company
    tickers: list[str] = []
    if company is not None:
        if company.tickers:
            tickers = list(company.tickers)
        elif company.ticker:
            tickers = [company.ticker]
    return {
        "entity_id": str(entity.id),
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "normalized_name": entity.normalized_name,
        "source_system": entity.source_system,
        "source_key": entity.source_key,
        "cik": company.cik if company is not None else None,
        "tickers": tickers,
        "legal_name": company.legal_name if company is not None else None,
        "exchange": company.exchange if company is not None else None,
    }


def upsert_entity_node(entity: Entity) -> None:
    params = _entity_params(entity)
    label = _LABEL[entity.entity_type]
    run_cypher(
        "MERGE (e:Entity {entity_id: $entity_id}) "
        "SET e.entity_type = $entity_type, e.canonical_name = $canonical_name, "
        "e.normalized_name = $normalized_name, e.source_system = $source_system, "
        "e.source_key = $source_key, e.cik = $cik, e.tickers = $tickers, "
        "e.legal_name = $legal_name, e.exchange = $exchange",
        params,
    )
    # Label is from EntityType, never from a request string.
    run_cypher(f"MATCH (e:Entity {{entity_id: $entity_id}}) SET e:{label}", {"entity_id": params["entity_id"]})


def upsert_relationship_edge(edge: Relationship) -> None:
    evidence = edge.evidence_links[0] if edge.evidence_links else None
    evidence_id = str(evidence.evidence_id) if evidence else None
    source_id = str(evidence.evidence.source_id) if evidence and evidence.evidence else None
    rel_type = edge.relationship_type  # closed vocabulary from Postgres CHECK
    run_cypher(
        "MATCH (a:Entity {entity_id: $from_id}), (b:Entity {entity_id: $to_id}) "
        f"MERGE (a)-[r:{rel_type} {{relationship_id: $relationship_id}}]->(b) "
        "SET r.confidence = $confidence, r.source_id = $source_id, r.evidence_id = $evidence_id, "
        "r.valid_from = $valid_from, r.valid_to = $valid_to, r.provenance_kind = $provenance_kind, "
        "r.status = $status",
        {
            "from_id": str(edge.subject_entity_id),
            "to_id": str(edge.object_entity_id),
            "relationship_id": str(edge.id),
            "confidence": edge.confidence,
            "source_id": source_id,
            "evidence_id": evidence_id,
            "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
            "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
            "provenance_kind": edge.provenance_kind,
            "status": edge.status,
        },
    )


def event_link_id(event_id: uuid.UUID, entity_id: uuid.UUID, role: str) -> str:
    return f"{event_id}:{entity_id}:{role}"


def upsert_event_node(event: Event) -> None:
    run_cypher(
        "MERGE (e:CanonicalEvent {event_id: $event_id}) "
        "SET e.event_type = $event_type, e.title = $title, e.status = $status, "
        "e.occurred_at = $occurred_at, e.published_at = $published_at, e.source_id = $source_id",
        {
            "event_id": str(event.id),
            "event_type": event.event_type,
            "title": event.title,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "published_at": event.published_at.isoformat() if event.published_at else None,
            "source_id": str(event.primary_source_id) if event.primary_source_id else None,
        },
    )


def upsert_event_link(link: EventEntity) -> None:
    run_cypher(
        "MATCH (e:CanonicalEvent {event_id: $event_id}), (n:Entity {entity_id: $entity_id}) "
        "MERGE (e)-[r:INVOLVES {link_id: $link_id}]->(n) "
        "SET r.role = $role, r.source_id = $source_id, r.evidence_id = $evidence_id, "
        "r.confidence = $confidence",
        {
            "event_id": str(link.event_id),
            "entity_id": str(link.entity_id),
            "link_id": event_link_id(link.event_id, link.entity_id, link.role),
            "role": link.role,
            "source_id": str(link.source_id) if link.source_id else None,
            "evidence_id": str(link.evidence_id) if link.evidence_id else None,
            "confidence": link.confidence,
        },
    )


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
    events: int = 0,
    event_links: int = 0,
) -> None:
    db.add(
        GraphProjectionRun(
            operation=operation,
            status=status,
            entities_projected=entities,
            relationships_projected=relationships,
            events_projected=events,
            event_links_projected=event_links,
            failures=failures,
            source=source,
            error=error,
            started_at=started_at,
            finished_at=_now(),
        )
    )


def project(
    db: Session,
    *,
    entity_ids: Optional[Sequence[uuid.UUID]] = None,
    relationship_ids: Optional[Sequence[uuid.UUID]] = None,
    init_schema: bool = True,
    source: str = "cli",
) -> dict:
    started = time.perf_counter()
    started_at = _now()
    with pipeline_bind(component="graph", operation="project"):
        try:
            if init_schema:
                ensure_schema()
            entities = _load_entities(db, entity_ids)
            edges = _load_relationships(db, relationship_ids)
            events = _load_events(db)
            links = _load_event_links(db)
            projected_e = projected_r = projected_ev = projected_el = failures = 0
            for entity in entities:
                try:
                    upsert_entity_node(entity)
                    _record(db, "entity", entity.id, "success")
                    projected_e += 1
                    GRAPH_METRICS.inc("graph_entities_projected_total", kind="entity", status="success")
                except Exception as exc:
                    failures += 1
                    _record(db, "entity", entity.id, "failed", type(exc).__name__)
                    GRAPH_METRICS.inc("graph_projection_failures_total", kind="entity", status="failed")
                    log_event(logger, 40, "GRAPH_ENTITY_PROJECT_FAILED", entity_type=entity.entity_type, status="failed")
            for edge in edges:
                try:
                    upsert_relationship_edge(edge)
                    _record(db, "relationship", edge.id, "success")
                    projected_r += 1
                    GRAPH_METRICS.inc("graph_relationships_projected_total", kind="relationship", status="success")
                except Exception as exc:
                    failures += 1
                    _record(db, "relationship", edge.id, "failed", type(exc).__name__)
                    GRAPH_METRICS.inc("graph_projection_failures_total", kind="relationship", status="failed")
                    log_event(
                        logger,
                        40,
                        "GRAPH_RELATIONSHIP_PROJECT_FAILED",
                        relationship_type=edge.relationship_type,
                        status="failed",
                    )
            for event in events:
                try:
                    upsert_event_node(event)
                    _record(db, "event", event.id, "success")
                    projected_ev += 1
                    GRAPH_METRICS.inc("graph_entities_projected_total", kind="event", status="success")
                except Exception as exc:
                    failures += 1
                    _record(db, "event", event.id, "failed", type(exc).__name__)
                    GRAPH_METRICS.inc("graph_projection_failures_total", kind="event", status="failed")
                    log_event(logger, 40, "GRAPH_EVENT_PROJECT_FAILED", event_type=event.event_type, status="failed")
            for link in links:
                try:
                    upsert_event_link(link)
                    _record(db, "event_link", _event_link_record_id(link), "success")
                    projected_el += 1
                    GRAPH_METRICS.inc("graph_relationships_projected_total", kind="event_link", status="success")
                except Exception as exc:
                    failures += 1
                    _record(db, "event_link", _event_link_record_id(link), "failed", type(exc).__name__)
                    GRAPH_METRICS.inc("graph_projection_failures_total", kind="event_link", status="failed")
                    log_event(logger, 40, "GRAPH_EVENT_LINK_PROJECT_FAILED", status="failed")
            duration = time.perf_counter() - started
            status = "success" if failures == 0 else "partial"
            GRAPH_METRICS.inc("graph_projection_total", operation="project", status="ok" if failures == 0 else "partial")
            GRAPH_METRICS.observe("graph_projection_duration_seconds", duration, operation="project")
            _checkpoint(
                db,
                operation="project",
                status=status,
                entities=projected_e,
                relationships=projected_r,
                events=projected_ev,
                event_links=projected_el,
                failures=failures,
                started_at=started_at,
                source=source,
            )
            db.commit()
            log_event(
                logger,
                20,
                "GRAPH_PROJECTED",
                component="graph",
                operation="project",
                status="ok" if failures == 0 else "partial",
                duration_ms=int(duration * 1000),
            )
            return {
                "ok": failures == 0,
                "entities": projected_e,
                "relationships": projected_r,
                "events": projected_ev,
                "event_links": projected_el,
                "failures": failures,
                "duration_seconds": round(duration, 3),
            }
        except GraphUnavailable:
            GRAPH_METRICS.inc("graph_projection_total", operation="project", status="unavailable")
            GRAPH_METRICS.inc("graph_projection_failures_total", kind="projection", status="unavailable")
            db.rollback()
            raise


def _load_entities(db: Session, ids: Optional[Sequence[uuid.UUID]]) -> List[Entity]:
    stmt = select(Entity).options(selectinload(Entity.company), selectinload(Entity.technology))
    if ids is not None:
        stmt = stmt.where(Entity.id.in_(list(ids)))
    return list(db.scalars(stmt).unique())


def _load_relationships(db: Session, ids: Optional[Sequence[uuid.UUID]]) -> List[Relationship]:
    stmt = select(Relationship).options(
        selectinload(Relationship.evidence_links).selectinload(RelationshipEvidence.evidence)
    )
    if ids is not None:
        stmt = stmt.where(Relationship.id.in_(list(ids)))
    return list(db.scalars(stmt).unique())


def _load_events(db: Session) -> List[Event]:
    return list(db.scalars(select(Event).where(Event.status == EventStatus.CONFIRMED.value)))


def _load_event_links(db: Session) -> List[EventEntity]:
    return list(
        db.scalars(
            select(EventEntity)
            .join(Event, Event.id == EventEntity.event_id)
            .where(Event.status == EventStatus.CONFIRMED.value)
        )
    )


def _event_link_record_id(link: EventEntity) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, event_link_id(link.event_id, link.entity_id, link.role))


def pending_counts(db: Session) -> dict:
    from sqlalchemy import func

    entities = db.scalar(select(func.count()).select_from(Entity)) or 0
    relationships = db.scalar(select(func.count()).select_from(Relationship)) or 0
    events = db.scalar(
        select(func.count()).select_from(Event).where(Event.status == EventStatus.CONFIRMED.value)
    ) or 0
    event_links = db.scalar(
        select(func.count())
        .select_from(EventEntity)
        .join(Event, Event.id == EventEntity.event_id)
        .where(Event.status == EventStatus.CONFIRMED.value)
    ) or 0
    projected = {
        kind: db.scalar(
            select(func.count()).select_from(GraphProjection).where(
                GraphProjection.record_kind == kind, GraphProjection.status == "success"
            )
        )
        or 0
        for kind in ("entity", "relationship", "event", "event_link")
    }
    failed = db.scalar(
        select(func.count()).select_from(GraphProjection).where(GraphProjection.status == "failed")
    ) or 0
    pending = (
        (entities - projected["entity"])
        + (relationships - projected["relationship"])
        + (events - projected["event"])
        + (event_links - projected["event_link"])
    )
    GRAPH_METRICS.set("graph_sync_pending", float(max(pending, 0)), component="graph")
    return {
        "entities": entities,
        "relationships": relationships,
        "events": events,
        "event_links": event_links,
        "projected_entities": projected["entity"],
        "projected_relationships": projected["relationship"],
        "projected_events": projected["event"],
        "projected_event_links": projected["event_link"],
        "pending": max(pending, 0),
        "failed": failed,
    }

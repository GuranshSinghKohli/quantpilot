"""Bounded graph reads. Depth-1 from Postgres; deeper hops need Neo4j."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.domain.models import Entity, EntityAlias, Relationship, RelationshipCandidateRecord, RelationshipEvidence
from app.graph.errors import GraphUnavailable
from app.graph.store import neo4j_configured, run_cypher

MAX_DEPTH = 3
MAX_RESULTS = 50


def get_entity(db: Session, entity_id: uuid.UUID) -> Optional[Entity]:
    return db.get(Entity, entity_id)


def entity_payload(entity: Entity) -> Dict[str, Any]:
    company = entity.company
    technology = entity.technology
    return {
        "entity_id": str(entity.id),
        "entity_type": entity.entity_type,
        "canonical_name": entity.canonical_name,
        "normalized_name": entity.normalized_name,
        "source_system": entity.source_system,
        "source_key": entity.source_key,
        "cik": company.cik if company is not None else None,
        "tickers": list(company.tickers or ([company.ticker] if company.ticker else []))
        if company is not None
        else None,
        "ticker": company.ticker if company is not None else None,
        "legal_name": company.legal_name if company is not None else None,
        "exchange": company.exchange if company is not None else None,
        "technology_slug": technology.slug if technology is not None else None,
    }


def entity_aliases(db: Session, entity_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    stmt = select(EntityAlias).where(EntityAlias.entity_id == entity_id).limit(min(limit, MAX_RESULTS))
    return [
        {
            "alias": row.alias,
            "normalized_alias": row.normalized_alias,
            "alias_type": row.alias_type,
            "confidence": row.confidence,
        }
        for row in db.scalars(stmt)
    ]


def entity_provenance(db: Session, entity_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    entity = db.get(Entity, entity_id)
    if entity is None:
        return None
    company = entity.company
    return {
        "entity_id": str(entity.id),
        "source_system": entity.source_system,
        "source_key": entity.source_key,
        "cik": company.cik if company is not None else None,
        "legal_name": company.legal_name if company is not None else None,
        "exchange": company.exchange if company is not None else None,
    }


def relationship_provenance(db: Session, relationship_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    stmt = (
        select(Relationship)
        .options(selectinload(Relationship.evidence_links).selectinload(RelationshipEvidence.evidence))
        .where(Relationship.id == relationship_id)
    )
    edge = db.scalars(stmt).first()
    if edge is None:
        return None
    return _edge_payload(edge)


def relationship_candidates(
    db: Session,
    entity_id: Optional[uuid.UUID] = None,
    *,
    limit: int = MAX_RESULTS,
) -> List[Dict[str, Any]]:
    stmt = select(RelationshipCandidateRecord)
    if entity_id is not None:
        stmt = stmt.where(
            or_(
                RelationshipCandidateRecord.from_entity_id == entity_id,
                RelationshipCandidateRecord.to_entity_id == entity_id,
            )
        )
    stmt = stmt.limit(min(limit, MAX_RESULTS))
    return [
        {
            "candidate_id": str(row.id),
            "from_entity_id": str(row.from_entity_id),
            "relationship_type": row.relationship_type,
            "to_entity_id": str(row.to_entity_id),
            "status": row.status,
            "confidence": row.confidence,
            "provenance_kind": row.provenance_kind,
            "source_id": str(row.source_id),
            "evidence_id": str(row.evidence_id),
            "accepted_relationship_id": str(row.accepted_relationship_id) if row.accepted_relationship_id else None,
        }
        for row in db.scalars(stmt)
    ]


def list_relationships(db: Session, entity_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    stmt = (
        select(Relationship)
        .options(selectinload(Relationship.evidence_links).selectinload(RelationshipEvidence.evidence))
        .where(
            or_(
                Relationship.subject_entity_id == entity_id,
                Relationship.object_entity_id == entity_id,
            )
        )
        .limit(min(limit, MAX_RESULTS))
    )
    return [_edge_payload(edge) for edge in db.scalars(stmt).unique()]


def direct_relationship(
    db: Session,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    relationship_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    stmt = (
        select(Relationship)
        .options(selectinload(Relationship.evidence_links).selectinload(RelationshipEvidence.evidence))
        .where(
            Relationship.subject_entity_id == from_id,
            Relationship.object_entity_id == to_id,
        )
    )
    if relationship_type:
        stmt = stmt.where(Relationship.relationship_type == relationship_type)
    return [_edge_payload(edge) for edge in db.scalars(stmt).unique()]


def neighbors_postgres(db: Session, entity_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    edges = list_relationships(db, entity_id, limit=limit)
    other_ids = []
    for edge in edges:
        other = edge["to_entity_id"] if edge["from_entity_id"] == str(entity_id) else edge["from_entity_id"]
        other_ids.append(uuid.UUID(other))
    found = {e.id: e for e in db.scalars(select(Entity).where(Entity.id.in_(other_ids))).all()} if other_ids else {}
    out = []
    for edge in edges:
        other = edge["to_entity_id"] if edge["from_entity_id"] == str(entity_id) else edge["from_entity_id"]
        entity = found.get(uuid.UUID(other))
        if entity is None:
            continue
        item = entity_payload(entity)
        item["relationship"] = edge
        item["relationship_type"] = edge["relationship_type"]
        item["relationship_id"] = edge["relationship_id"]
        item["provenance_kind"] = edge["provenance_kind"]
        item["confidence"] = edge["confidence"]
        out.append(item)
    return out[:limit]


def neighbors(
    db: Session,
    entity_id: uuid.UUID,
    *,
    depth: int = 1,
    limit: int = MAX_RESULTS,
) -> List[Dict[str, Any]]:
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")
    limit = min(limit, MAX_RESULTS)
    if depth == 1:
        return neighbors_postgres(db, entity_id, limit=limit)
    if not neo4j_configured():
        raise GraphUnavailable("depth > 1 requires Neo4j")
    hops = {1: "[*1..1]", 2: "[*1..2]", 3: "[*1..3]"}[depth]
    rows = run_cypher(
        "MATCH (start:Entity {entity_id: $entity_id}) "
        f"MATCH path = (start)-{hops}-(seen:Entity) "
        "WHERE seen.entity_id <> $entity_id "
        "WITH seen, min(length(path)) AS hops "
        "RETURN seen.entity_id AS entity_id, seen.entity_type AS entity_type, "
        "seen.canonical_name AS canonical_name, hops "
        "ORDER BY hops, seen.canonical_name "
        "LIMIT $limit",
        {"entity_id": str(entity_id), "limit": limit},
    )
    return rows


def companies_for_technology(db: Session, technology_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    tech = db.get(Entity, technology_id)
    if tech is None or tech.entity_type != "technology":
        return []
    stmt = (
        select(Relationship)
        .where(
            Relationship.object_entity_id == technology_id,
            Relationship.relationship_type.in_(("USES", "DEVELOPS", "DEPENDS_ON", "OWNS")),
        )
        .limit(min(limit, MAX_RESULTS))
    )
    company_ids = [edge.subject_entity_id for edge in db.scalars(stmt)]
    companies = db.scalars(select(Entity).where(Entity.id.in_(company_ids))).all() if company_ids else []
    return [entity_payload(c) for c in companies]


def technologies_for_company(db: Session, company_id: uuid.UUID, *, limit: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    stmt = (
        select(Relationship)
        .where(
            Relationship.subject_entity_id == company_id,
            Relationship.relationship_type.in_(("USES", "DEVELOPS", "DEPENDS_ON", "OWNS")),
        )
        .limit(min(limit, MAX_RESULTS))
    )
    tech_ids = [edge.object_entity_id for edge in db.scalars(stmt)]
    techs = db.scalars(select(Entity).where(Entity.id.in_(tech_ids))).all() if tech_ids else []
    return [entity_payload(t) for t in techs]


def _edge_payload(edge: Relationship) -> Dict[str, Any]:
    links = edge.evidence_links or []
    evidence_ids = [str(link.evidence_id) for link in links]
    source_ids = []
    for link in links:
        if link.evidence is not None:
            source_ids.append(str(link.evidence.source_id))
    return {
        "relationship_id": str(edge.id),
        "relationship_type": edge.relationship_type,
        "from_entity_id": str(edge.subject_entity_id),
        "to_entity_id": str(edge.object_entity_id),
        "status": edge.status,
        "provenance_kind": edge.provenance_kind,
        "verified": edge.provenance_kind == "VERIFIED" and bool(evidence_ids),
        "inferred": edge.provenance_kind == "INFERRED",
        "confidence": edge.confidence,
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
        "source_ids": source_ids,
        "evidence_ids": evidence_ids,
    }

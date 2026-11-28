"""Relationship edges and the evidence that supports or contradicts them."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import EvidenceStance, ProvenanceKind, RelationshipStatus
from app.domain.models import Relationship, RelationshipEvidence
from app.domain.schemas import RelationshipEvidenceIn, RelationshipIn
from app.repositories._upsert import insert_ignore


def _aware(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def derive_status(current: str, stances: Iterable[str]) -> str:
    """Status follows the evidence: both sides -> disputed, support only -> supported,
    none -> asserted. A retracted edge stays retracted until a human reopens it."""
    if current == RelationshipStatus.RETRACTED.value:
        return current
    seen = set(stances)
    supports = EvidenceStance.SUPPORTS.value in seen
    contradicts = EvidenceStance.CONTRADICTS.value in seen
    if supports and contradicts:
        return RelationshipStatus.DISPUTED.value
    if supports:
        return RelationshipStatus.SUPPORTED.value
    if contradicts:
        return RelationshipStatus.DISPUTED.value
    return RelationshipStatus.ASSERTED.value


def upsert_relationship(db: Session, data: RelationshipIn) -> Tuple[Relationship, bool]:
    """Keyed on (subject, type, object). Re-observing an edge extends last_seen_at."""
    created = insert_ignore(
        db,
        Relationship,
        {
            "id": uuid.uuid4(),
            "subject_entity_id": data.subject_entity_id,
            "relationship_type": data.relationship_type.value,
            "object_entity_id": data.object_entity_id,
            "status": RelationshipStatus.ASSERTED.value,
            "provenance_kind": ProvenanceKind.OBSERVED.value,
            "confidence": data.confidence,
            "valid_from": data.valid_from,
            "valid_to": data.valid_to,
            "first_seen_at": data.observed_at,
            "last_seen_at": data.observed_at,
        },
        ["subject_entity_id", "relationship_type", "object_entity_id"],
    )
    edge = db.scalars(
        select(Relationship).where(
            Relationship.subject_entity_id == data.subject_entity_id,
            Relationship.relationship_type == data.relationship_type.value,
            Relationship.object_entity_id == data.object_entity_id,
        )
    ).one()
    if not created:
        observed = _aware(data.observed_at)
        if observed > _aware(edge.last_seen_at):
            edge.last_seen_at = observed
        if observed < _aware(edge.first_seen_at):
            edge.first_seen_at = observed
        db.flush()
    return edge, created


def link_evidence(
    db: Session, relationship_id: uuid.UUID, data: RelationshipEvidenceIn
) -> Tuple[Relationship, bool]:
    """Attach evidence (idempotent on the pair) and recompute the edge status."""
    created = insert_ignore(
        db,
        RelationshipEvidence,
        {
            "relationship_id": relationship_id,
            "evidence_id": data.evidence_id,
            "stance": data.stance.value,
        },
        ["relationship_id", "evidence_id"],
    )
    edge = db.get(Relationship, relationship_id)
    if edge is None:
        raise LookupError(f"Relationship {relationship_id} not found")
    stances = db.scalars(
        select(RelationshipEvidence.stance).where(
            RelationshipEvidence.relationship_id == relationship_id
        )
    ).all()
    edge.status = derive_status(edge.status, stances)
    db.flush()
    return edge, created

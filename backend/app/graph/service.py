"""Canonical relationship write path. Postgres validates; Neo4j does not."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import (
    EvidenceStance,
    ProvenanceKind,
    RelationshipStatus,
    RelationshipType,
)
from app.domain.models import Entity, EvidenceRecord, Relationship, Source
from app.domain.schemas import RelationshipEvidenceIn, RelationshipIn
from app.graph.errors import ProvenanceRequired, RelationshipConflict
from app.graph.resolve import EntityRef, resolve_entity
from app.repositories import relationships as relationship_repo


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_entity: EntityRef
    relationship_type: RelationshipType
    to_entity: EntityRef
    source_id: uuid.UUID
    evidence_id: uuid.UUID
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    provenance_kind: ProvenanceKind = ProvenanceKind.OBSERVED
    observed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _validity(self) -> "RelationshipCandidate":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from must not be after valid_to")
        return self


def _require_provenance(db: Session, candidate: RelationshipCandidate) -> EvidenceRecord:
    evidence = db.get(EvidenceRecord, candidate.evidence_id)
    if evidence is None:
        raise ProvenanceRequired("evidence_id does not exist")
    if evidence.source_id != candidate.source_id:
        raise ProvenanceRequired("evidence_id does not belong to source_id")
    if db.get(Source, candidate.source_id) is None:
        raise ProvenanceRequired("source_id does not exist")
    if candidate.provenance_kind == ProvenanceKind.VERIFIED:
        if not evidence.excerpt.strip():
            raise ProvenanceRequired("verified relationships need a non-empty evidence excerpt")
    return evidence


def _strength(kind: str, confidence: Optional[float]) -> Tuple[int, float]:
    rank = {ProvenanceKind.VERIFIED.value: 2, ProvenanceKind.OBSERVED.value: 1, ProvenanceKind.INFERRED.value: 0}
    return rank.get(kind, 0), confidence if confidence is not None else -1.0


def persist_relationship(db: Session, candidate: RelationshipCandidate) -> Tuple[Relationship, bool]:
    """Validate, resolve, persist. Idempotent on the directed triple + evidence pair.

    Does not create the inverse edge. Does not treat INFERRED as fact.
    Does not overwrite a stronger/newer edge with a weaker one.
    """
    if candidate.from_entity == candidate.to_entity and candidate.from_entity.entity_id:
        raise ValueError("A relationship needs two different entities")
    evidence = _require_provenance(db, candidate)
    subject_id = resolve_entity(db, candidate.from_entity).require()
    object_id = resolve_entity(db, candidate.to_entity).require()
    if subject_id == object_id:
        raise ValueError("A relationship needs two different entities")
    if db.get(Entity, subject_id) is None or db.get(Entity, object_id) is None:
        raise ProvenanceRequired("both relationship endpoints must exist")

    observed = _aware(candidate.observed_at or evidence.extracted_at)
    incoming = RelationshipIn(
        subject_entity_id=subject_id,
        relationship_type=candidate.relationship_type,
        object_entity_id=object_id,
        observed_at=observed,
        confidence=candidate.confidence,
        valid_from=candidate.valid_from,
        valid_to=candidate.valid_to,
    )
    existing = db.scalars(
        select(Relationship).where(
            Relationship.subject_entity_id == subject_id,
            Relationship.relationship_type == candidate.relationship_type.value,
            Relationship.object_entity_id == object_id,
        )
    ).first()
    if existing is not None:
        if existing.status == RelationshipStatus.RETRACTED.value:
            raise RelationshipConflict("refusing to silently reopen a retracted relationship")
        incoming_rank = _strength(candidate.provenance_kind.value, candidate.confidence)
        existing_rank = _strength(existing.provenance_kind, existing.confidence)
        if incoming_rank < existing_rank:
            relationship_repo.link_evidence(
                db, existing.id, RelationshipEvidenceIn(evidence_id=evidence.id, stance=EvidenceStance.SUPPORTS)
            )
            return existing, False
        if (
            existing.provenance_kind == ProvenanceKind.VERIFIED.value
            and candidate.provenance_kind == ProvenanceKind.INFERRED
        ):
            raise RelationshipConflict("INFERRED must not replace a VERIFIED relationship")

    edge, created = relationship_repo.upsert_relationship(db, incoming)
    if created or incoming_rank_allows_update(existing, candidate):
        edge.provenance_kind = candidate.provenance_kind.value
        if candidate.confidence is not None:
            if existing is None or _strength(candidate.provenance_kind.value, candidate.confidence) >= _strength(
                existing.provenance_kind, existing.confidence
            ):
                edge.confidence = candidate.confidence
        db.flush()
    relationship_repo.link_evidence(
        db, edge.id, RelationshipEvidenceIn(evidence_id=evidence.id, stance=EvidenceStance.SUPPORTS)
    )
    db.flush()
    return edge, created


def incoming_rank_allows_update(existing: Optional[Relationship], candidate: RelationshipCandidate) -> bool:
    if existing is None:
        return True
    return _strength(candidate.provenance_kind.value, candidate.confidence) >= _strength(
        existing.provenance_kind, existing.confidence
    )

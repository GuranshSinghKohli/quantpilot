"""Relationship candidates are not graph edges until ACCEPTED."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.enums import CandidateStatus, ProvenanceKind, RelationshipType
from app.domain.models import (
    Entity,
    EvidenceRecord,
    Relationship,
    RelationshipCandidateRecord,
    Source,
)
from app.graph.errors import (
    AmbiguousEntity,
    EvidenceInsufficient,
    ProvenanceRequired,
    UnresolvedEntity,
)
from app.graph.metrics import GRAPH_METRICS
from app.graph.resolve import EntityRef, resolve_entity
from app.graph.service import RelationshipCandidate, persist_relationship
from app.observability.logger import get_logger, log_event

logger = get_logger("graph")

# Deterministic cues. Co-mention without one of these is not evidence.
_CUES: dict[str, tuple[str, ...]] = {
    RelationshipType.PARTNERED_WITH.value: ("partnership", "partner", "alliance", "relationship"),
    RelationshipType.ACQUIRED.value: ("acquir", "acquisition", "bought", "purchase"),
    RelationshipType.OWNS.value: ("owns", "ownership", "owned"),
    RelationshipType.INVESTED_IN.value: ("invest", "stake"),
    RelationshipType.SUPPLIES.value: ("suppl",),
    RelationshipType.DEVELOPS.value: ("develop",),
    RelationshipType.USES.value: ("uses", "using"),
    RelationshipType.DEPENDS_ON.value: ("depend",),
    RelationshipType.COMPETES_WITH.value: ("compet",),
    RelationshipType.CUSTOMER_OF.value: ("customer"),
    RelationshipType.CUSTOMERS_OF.value: ("customer"),
}

STRUCTURED_EXTRACTION = frozenset({"fixture", "structured"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CandidateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_entity: EntityRef
    relationship_type: RelationshipType
    to_entity: EntityRef
    source_id: uuid.UUID
    evidence_id: uuid.UUID
    confidence: float = Field(ge=0.0, le=1.0)
    provenance_kind: ProvenanceKind = ProvenanceKind.OBSERVED


def evidence_explicitly_states(excerpt: str, relationship_type: RelationshipType, method: str) -> bool:
    text = (excerpt or "").strip()
    if not text:
        return False
    if method in STRUCTURED_EXTRACTION:
        return True
    lowered = text.casefold()
    cues = _CUES.get(relationship_type.value, ())
    return _cue_affirmed(lowered, cues)


def _cue_affirmed(text: str, cues: tuple[str, ...]) -> bool:
    for cue in cues:
        start = 0
        while True:
            index = text.find(cue, start)
            if index < 0:
                break
            prefix = text[max(0, index - 40) : index]
            if not re.search(r"\b(no|not|never|without)\b", prefix):
                return True
            start = index + len(cue)
    return False


def _require_evidence(db: Session, proposal: CandidateProposal) -> EvidenceRecord:
    if not proposal.source_id or not proposal.evidence_id:
        raise ProvenanceRequired("candidate requires source_id and evidence_id")
    evidence = db.get(EvidenceRecord, proposal.evidence_id)
    if evidence is None:
        raise ProvenanceRequired("evidence_id does not exist")
    if evidence.source_id != proposal.source_id:
        raise ProvenanceRequired("evidence_id does not belong to source_id")
    if db.get(Source, proposal.source_id) is None:
        raise ProvenanceRequired("source_id does not exist")
    if not evidence_explicitly_states(evidence.excerpt, proposal.relationship_type, evidence.extraction_method):
        raise EvidenceInsufficient(
            "evidence does not explicitly state the relationship; co-mention is not enough"
        )
    return evidence


class RelationshipCandidateService:
    """Propose, accept, and reject candidates. Acceptance uses persist_relationship."""

    def propose(self, db: Session, proposal: CandidateProposal) -> Tuple[RelationshipCandidateRecord, bool]:
        evidence = _require_evidence(db, proposal)
        from_res = resolve_entity(db, proposal.from_entity)
        to_res = resolve_entity(db, proposal.to_entity)
        if from_res.status.value == "UNRESOLVED" or to_res.status.value == "UNRESOLVED":
            GRAPH_METRICS.inc("graph_candidates_total", operation="propose", status="unresolved")
            raise UnresolvedEntity("candidate endpoints could not be resolved")
        if from_res.status.value == "AMBIGUOUS" or to_res.status.value == "AMBIGUOUS":
            GRAPH_METRICS.inc(
                "graph_candidates_total",
                operation="propose",
                status=CandidateStatus.AMBIGUOUS.value,
                relationship_type=proposal.relationship_type.value,
            )
            GRAPH_METRICS.inc(
                "graph_candidates_ambiguous_total",
                operation="propose",
                status=CandidateStatus.AMBIGUOUS.value,
                relationship_type=proposal.relationship_type.value,
            )
            raise AmbiguousEntity("candidate endpoints are ambiguous; will not guess")
        from_id = from_res.require()
        to_id = to_res.require()
        if from_id == to_id:
            raise ValueError("A candidate needs two different entities")
        if db.get(Entity, from_id) is None or db.get(Entity, to_id) is None:
            raise ProvenanceRequired("both candidate endpoints must exist")

        existing = db.scalars(
            select(RelationshipCandidateRecord).where(
                RelationshipCandidateRecord.from_entity_id == from_id,
                RelationshipCandidateRecord.relationship_type == proposal.relationship_type.value,
                RelationshipCandidateRecord.to_entity_id == to_id,
                RelationshipCandidateRecord.evidence_id == evidence.id,
            )
        ).first()
        if existing is not None:
            GRAPH_METRICS.inc(
                "graph_candidates_total",
                operation="propose",
                status="duplicate",
                relationship_type=proposal.relationship_type.value,
            )
            return existing, False

        row = RelationshipCandidateRecord(
            id=uuid.uuid4(),
            from_entity_id=from_id,
            relationship_type=proposal.relationship_type.value,
            to_entity_id=to_id,
            source_id=proposal.source_id,
            evidence_id=evidence.id,
            confidence=proposal.confidence,
            provenance_kind=proposal.provenance_kind.value,
            status=CandidateStatus.PENDING.value,
            discovered_at=_now(),
        )
        db.add(row)
        db.flush()
        GRAPH_METRICS.inc(
            "graph_candidates_total",
            operation="propose",
            status=CandidateStatus.PENDING.value,
            relationship_type=proposal.relationship_type.value,
        )
        log_event(
            logger,
            20,
            "GRAPH_CANDIDATE_CREATED",
            component="graph",
            operation="propose",
            status=row.status,
            relationship_type=row.relationship_type,
        )
        return row, True

    def accept(self, db: Session, candidate_id: uuid.UUID) -> Tuple[RelationshipCandidateRecord, Relationship]:
        row = db.get(RelationshipCandidateRecord, candidate_id)
        if row is None:
            raise ProvenanceRequired("candidate does not exist")
        if row.status == CandidateStatus.REJECTED.value:
            raise ProvenanceRequired("a rejected candidate cannot be accepted")
        if row.status == CandidateStatus.AMBIGUOUS.value:
            raise AmbiguousEntity("ambiguous candidate will not be accepted")
        edge, _created = persist_relationship(
            db,
            RelationshipCandidate(
                from_entity=EntityRef(entity_id=row.from_entity_id),
                relationship_type=RelationshipType(row.relationship_type),
                to_entity=EntityRef(entity_id=row.to_entity_id),
                source_id=row.source_id,
                evidence_id=row.evidence_id,
                confidence=row.confidence,
                provenance_kind=ProvenanceKind(row.provenance_kind),
            ),
        )
        row.status = CandidateStatus.ACCEPTED.value
        row.accepted_relationship_id = edge.id
        row.reviewed_at = _now()
        db.flush()
        GRAPH_METRICS.inc(
            "graph_candidates_accepted_total",
            operation="accept",
            status=CandidateStatus.ACCEPTED.value,
            relationship_type=row.relationship_type,
        )
        log_event(
            logger,
            20,
            "GRAPH_CANDIDATE_ACCEPTED",
            component="graph",
            operation="accept",
            status=row.status,
            relationship_type=row.relationship_type,
        )
        return row, edge

    def reject(self, db: Session, candidate_id: uuid.UUID) -> RelationshipCandidateRecord:
        row = db.get(RelationshipCandidateRecord, candidate_id)
        if row is None:
            raise ProvenanceRequired("candidate does not exist")
        if row.status == CandidateStatus.ACCEPTED.value:
            raise ProvenanceRequired("an accepted candidate cannot be rejected")
        row.status = CandidateStatus.REJECTED.value
        row.reviewed_at = _now()
        db.flush()
        GRAPH_METRICS.inc(
            "graph_candidates_rejected_total",
            operation="reject",
            status=CandidateStatus.REJECTED.value,
            relationship_type=row.relationship_type,
        )
        log_event(
            logger,
            20,
            "GRAPH_CANDIDATE_REJECTED",
            component="graph",
            operation="reject",
            status=row.status,
            relationship_type=row.relationship_type,
        )
        return row

    def get(self, db: Session, candidate_id: uuid.UUID) -> Optional[RelationshipCandidateRecord]:
        return db.get(RelationshipCandidateRecord, candidate_id)

    def list_for_entity(
        self,
        db: Session,
        entity_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> List[RelationshipCandidateRecord]:
        stmt = (
            select(RelationshipCandidateRecord)
            .where(
                or_(
                    RelationshipCandidateRecord.from_entity_id == entity_id,
                    RelationshipCandidateRecord.to_entity_id == entity_id,
                )
            )
            .limit(limit)
        )
        return list(db.scalars(stmt))

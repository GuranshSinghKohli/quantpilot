"""Conservative, deterministic entity resolution. Never guesses."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AliasType, EntityType, ResolutionStatus
from app.domain.models import Company, Entity, EntityAlias
from app.graph.errors import AmbiguousEntity, UnresolvedEntity
from app.graph.metrics import GRAPH_METRICS
from app.graph.normalize import normalize_cik, normalize_name, normalize_ticker
from app.observability.logger import get_logger, log_event

logger = get_logger("graph")


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: Optional[uuid.UUID] = None
    entity_type: Optional[EntityType] = None
    source_system: Optional[str] = None
    source_key: Optional[str] = None
    cik: Optional[str] = None
    ticker: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Resolution:
    status: ResolutionStatus
    entity_id: Optional[uuid.UUID] = None
    candidate_ids: List[uuid.UUID] = field(default_factory=list)
    matched_on: Optional[str] = None

    @property
    def matched_by(self) -> Optional[str]:
        return self.matched_on

    @property
    def candidates(self) -> List[uuid.UUID]:
        return list(self.candidate_ids)

    def public_dict(self) -> dict:
        return {
            "status": self.status.value,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "matched_by": self.matched_on,
            "candidates": [str(item) for item in self.candidate_ids],
        }

    def require(self) -> uuid.UUID:
        if self.status == ResolutionStatus.UNRESOLVED:
            raise UnresolvedEntity("entity could not be resolved")
        if self.status == ResolutionStatus.AMBIGUOUS:
            raise AmbiguousEntity(
                f"entity is ambiguous ({len(self.candidate_ids)} candidates); will not guess"
            )
        assert self.entity_id is not None
        return self.entity_id


def _done(ids: Sequence[uuid.UUID], matched_on: str) -> Resolution:
    unique = list(dict.fromkeys(ids))
    if not unique:
        return Resolution(ResolutionStatus.UNRESOLVED, matched_on=matched_on)
    if len(unique) > 1:
        return Resolution(ResolutionStatus.AMBIGUOUS, candidate_ids=unique, matched_on=matched_on)
    return Resolution(ResolutionStatus.RESOLVED, entity_id=unique[0], candidate_ids=unique, matched_on=matched_on)


def _record(result: Resolution, started: float, entity_type: Optional[str]) -> Resolution:
    duration = time.perf_counter() - started
    GRAPH_METRICS.inc(
        "entity_resolution_total",
        operation="resolve",
        status=result.status.value,
        entity_type=entity_type or "any",
    )
    if result.status != ResolutionStatus.RESOLVED:
        GRAPH_METRICS.inc(
            "entity_resolution_failures_total",
            operation="resolve",
            status=result.status.value,
            entity_type=entity_type or "any",
        )
    log_event(
        logger,
        20,
        "ENTITY_RESOLVED",
        component="graph",
        operation="resolve",
        status=result.status.value,
        entity_type=entity_type,
        duration_ms=int(duration * 1000),
    )
    return result


def resolve_entity(db: Session, ref: EntityRef) -> Resolution:
    """exact source identity → authoritative identifier → normalized name → alias.

    No fuzzy match. Multiple hits → AMBIGUOUS. Zero hits → UNRESOLVED.
    """
    started = time.perf_counter()
    entity_type = ref.entity_type.value if ref.entity_type else None

    if ref.entity_id is not None:
        found = db.get(Entity, ref.entity_id)
        if found is None:
            return _record(Resolution(ResolutionStatus.UNRESOLVED, matched_on="entity_id"), started, entity_type)
        return _record(
            Resolution(ResolutionStatus.RESOLVED, entity_id=found.id, candidate_ids=[found.id], matched_on="entity_id"),
            started,
            found.entity_type,
        )

    if ref.cik:
        cik = normalize_cik(ref.cik)
        ids = db.scalars(select(Company.entity_id).where(Company.cik == cik)).all()
        result = _done(ids, "cik")
        if result.status != ResolutionStatus.UNRESOLVED:
            return _record(result, started, entity_type or "company")

    if ref.source_system and ref.source_key:
        key = ref.source_key
        if ref.source_system == "sec":
            key = normalize_cik(ref.source_key)
        stmt = select(Entity.id).where(
            Entity.source_system == ref.source_system,
            Entity.source_key == key,
        )
        if ref.entity_type is not None:
            stmt = stmt.where(Entity.entity_type == ref.entity_type.value)
        result = _done(db.scalars(stmt).all(), "source_key")
        if result.status != ResolutionStatus.UNRESOLVED:
            return _record(result, started, entity_type)

    if ref.name:
        norm = normalize_name(ref.name)
        stmt = select(Entity.id).where(Entity.normalized_name == norm)
        if ref.entity_type is not None:
            stmt = stmt.where(Entity.entity_type == ref.entity_type.value)
        ids = list(db.scalars(stmt).all())
        alias_stmt = select(EntityAlias.entity_id).where(
            EntityAlias.normalized_alias == norm,
            EntityAlias.alias_type.in_([AliasType.LEGAL_NAME.value, AliasType.DISPLAY_NAME.value]),
        )
        if ref.entity_type is not None:
            alias_stmt = alias_stmt.join(Entity, Entity.id == EntityAlias.entity_id).where(
                Entity.entity_type == ref.entity_type.value
            )
        ids.extend(db.scalars(alias_stmt).all())
        result = _done(ids, "name")
        if result.status != ResolutionStatus.UNRESOLVED:
            return _record(result, started, entity_type)

    if ref.ticker:
        symbol = normalize_ticker(ref.ticker)
        stmt = select(EntityAlias.entity_id).where(
            EntityAlias.alias_type == AliasType.TICKER.value,
            EntityAlias.normalized_alias == symbol,
        )
        if ref.entity_type is not None:
            stmt = stmt.join(Entity, Entity.id == EntityAlias.entity_id).where(
                Entity.entity_type == ref.entity_type.value
            )
        result = _done(db.scalars(stmt).all(), "ticker")
        if result.status != ResolutionStatus.UNRESOLVED:
            return _record(result, started, entity_type)
        company_ids = db.scalars(select(Company.entity_id).where(Company.ticker == symbol)).all()
        return _record(_done(company_ids, "ticker"), started, entity_type or "company")

    return _record(Resolution(ResolutionStatus.UNRESOLVED), started, entity_type)

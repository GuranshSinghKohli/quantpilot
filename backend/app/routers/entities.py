"""Read-only entity / relationship API. No Cypher is accepted from clients."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.domain.enums import RelationshipType
from app.graph.errors import GraphUnavailable
from app.graph.queries import (
    MAX_DEPTH,
    MAX_RESULTS,
    companies_for_technology,
    direct_relationship,
    entity_payload,
    get_entity,
    list_relationships,
    neighbors,
    technologies_for_company,
)

router = APIRouter(tags=["entities"])


class EntityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_type: str
    canonical_name: str
    normalized_name: str
    source_system: Optional[str] = None
    source_key: Optional[str] = None
    cik: Optional[str] = None
    tickers: Optional[list] = None
    ticker: Optional[str] = None
    technology_slug: Optional[str] = None


def _entity_or_404(db: Session, entity_id: uuid.UUID):
    entity = get_entity(db, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return entity


@router.get("/entities/{entity_id}", response_model=EntityOut)
def read_entity(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> EntityOut:
    return EntityOut(**entity_payload(_entity_or_404(db, entity_id)))


@router.get("/entities/{entity_id}/relationships")
def read_relationships(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=MAX_RESULTS),
) -> dict:
    _entity_or_404(db, entity_id)
    return {"entity_id": str(entity_id), "relationships": list_relationships(db, entity_id, limit=limit)}


@router.get("/entities/{entity_id}/neighbors")
def read_neighbors(
    entity_id: uuid.UUID,
    db: Session = Depends(get_db),
    depth: int = Query(default=1, ge=1, le=MAX_DEPTH),
    limit: int = Query(default=50, ge=1, le=MAX_RESULTS),
) -> dict:
    _entity_or_404(db, entity_id)
    try:
        items = neighbors(db, entity_id, depth=depth, limit=limit)
    except GraphUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"entity_id": str(entity_id), "depth": depth, "neighbors": items}


@router.get("/entities/{entity_id}/technologies")
def read_technologies(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    _entity_or_404(db, entity_id)
    return {"entity_id": str(entity_id), "technologies": technologies_for_company(db, entity_id)}


@router.get("/technologies/{entity_id}/companies")
def read_technology_companies(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    entity = _entity_or_404(db, entity_id)
    if entity.entity_type != "technology":
        raise HTTPException(status_code=404, detail="technology not found")
    return {"entity_id": str(entity_id), "companies": companies_for_technology(db, entity_id)}


@router.get("/graph/relationship")
def read_direct_relationship(
    from_entity_id: uuid.UUID = Query(...),
    to_entity_id: uuid.UUID = Query(...),
    relationship_type: Optional[RelationshipType] = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    rel_type = relationship_type.value if relationship_type else None
    return {
        "from_entity_id": str(from_entity_id),
        "to_entity_id": str(to_entity_id),
        "relationships": direct_relationship(db, from_entity_id, to_entity_id, rel_type),
    }

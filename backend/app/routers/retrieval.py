"""Read-only retrieval API. No raw SQL, Cypher, or embedding vectors."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.settings import get_settings
from app.graph.queries import MAX_RESULTS
from app.retrieval.package import RequestOversized, ResearchRequest, assemble_package
from app.retrieval.search import hit_payload, search

router = APIRouter(tags=["retrieval"])


class ContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    entity_ids: List[uuid.UUID] = Field(default_factory=list)
    event_ids: List[uuid.UUID] = Field(default_factory=list)
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    document_types: List[str] = Field(default_factory=list)
    max_documents: Optional[int] = None
    max_events: Optional[int] = None
    max_relationships: Optional[int] = None
    max_entities: Optional[int] = None
    top_k: Optional[int] = None


@router.get("/search")
def read_search(
    q: str = Query(min_length=1, max_length=2000),
    entity_id: Optional[uuid.UUID] = None,
    event_id: Optional[uuid.UUID] = None,
    document_type: Optional[str] = None,
    top_k: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if top_k > settings.RETRIEVAL_MAX_TOP_K:
        raise HTTPException(status_code=400, detail="top_k exceeds configured ceiling")
    result = search(
        db,
        q,
        entity_ids=[entity_id] if entity_id else None,
        event_ids=[event_id] if event_id else None,
        document_types=[document_type] if document_type else None,
        top_k=min(top_k, MAX_RESULTS),
    )
    return {
        "query": q,
        "available": result["available"],
        "reason": result["reason"],
        "results": [hit_payload(hit) for hit in result["hits"]],
    }


@router.post("/research/context")
def build_context(body: ContextBody, db: Session = Depends(get_db)) -> dict:
    try:
        request = ResearchRequest.model_validate(body.model_dump())
        return assemble_package(db, request)
    except RequestOversized as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

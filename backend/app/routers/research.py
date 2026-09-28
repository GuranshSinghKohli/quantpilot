"""Bounded research API. Returns ResearchResult. No SQL, Cypher, or raw vectors."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.research.llm import LLMError
from app.research.service import ResearchUnavailable, run_research
from app.retrieval.package import RequestOversized, ResearchRequest

router = APIRouter(tags=["research"])


class ResearchBody(BaseModel):
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


@router.post("/research")
def create_research(body: ResearchBody, db: Session = Depends(get_db)) -> dict:
    try:
        request = ResearchRequest.model_validate(body.model_dump())
        result = run_research(db, request)
        return result.model_dump(mode="json")
    except RequestOversized as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ResearchUnavailable, LLMError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "llm_unavailable", "message": str(exc)[:200]},
        ) from exc

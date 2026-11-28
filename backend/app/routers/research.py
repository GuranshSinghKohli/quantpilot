"""Research API. POST submits a durable job. GET reads it. The graph runs in the worker."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pipeline_context import new_correlation_id
from app.core.settings import get_settings
from app.repositories.research_jobs import IdempotencyConflict
from app.research.outbox import QueueFull
from app.research.submit import research_view, submit_research
from app.retrieval.package import RequestOversized, ResearchRequest, _limits
from app.security.request_context import get_request_id

router = APIRouter(tags=["research"])


def _as_text(value: Optional[str]) -> str:
    return value if isinstance(value, str) else ""


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
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)


@router.post("/research", status_code=202)
def create_research(
    body: ResearchBody,
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
) -> dict:
    try:
        request = ResearchRequest.model_validate(body.model_dump(exclude={"idempotency_key"}))
        _limits(request)
        key = (_as_text(idempotency_key) or body.idempotency_key or uuid.uuid4().hex)[:128]
        cid = (_as_text(correlation_id) or get_request_id() or new_correlation_id())[:128]
        job = submit_research(
            db,
            request,
            owner_key="research-api",
            idempotency_key=key,
            correlation_id=cid,
            settings=get_settings(),
        )
        db.commit()
    except RequestOversized as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QueueFull as exc:
        raise HTTPException(status_code=429, detail="research queue is full") from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:200]) from exc
    return {
        "research_id": str(job.id),
        "status": job.status,
        "correlation_id": job.correlation_id,
        "status_url": f"/api/research/{job.id}",
    }


@router.get("/research/{research_id}")
def read_research(research_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    view = research_view(db, research_id)
    if view is None:
        raise HTTPException(status_code=404, detail="research job not found")
    return view

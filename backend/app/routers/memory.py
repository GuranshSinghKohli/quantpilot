from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_optional_user
from app.db.models import User
from app.db.session import get_db
from app.memory import chroma_store
from app.services import analysis_history_store

router = APIRouter(tags=["memory"])


class SessionHistoryEntry(BaseModel):
    ticker: str
    timestamp: str
    recommendation: str
    risk_level: str


class StoredReportItem(BaseModel):
    id: str
    metadata: Dict[str, Any]
    report: Dict[str, Any]
    distance: Optional[float] = None


class SearchResponse(BaseModel):
    query: str
    results: List[StoredReportItem]


def _owner_or_empty(
    user: Optional[User],
    x_guest_id: Optional[str],
) -> Optional[str]:
    return analysis_history_store.resolve_owner_key(user, x_guest_id)


@router.get("/memory/history", response_model=List[SessionHistoryEntry])
async def get_memory_history(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> List[SessionHistoryEntry]:
    owner_key = _owner_or_empty(user, x_guest_id)
    if not owner_key:
        return []
    history = analysis_history_store.list_recent(db, owner_key)
    return [SessionHistoryEntry.model_validate(entry) for entry in history]


@router.get("/memory/reports/{ticker}", response_model=List[StoredReportItem])
async def get_reports_for_ticker(
    ticker: str,
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> List[StoredReportItem]:
    symbol = ticker.upper().strip()
    owner_key = _owner_or_empty(user, x_guest_id)
    if not owner_key:
        return []
    try:
        reports = chroma_store.get_by_ticker(symbol, owner_key=owner_key)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to retrieve reports: {exc}",
        ) from exc

    return [
        StoredReportItem(
            id=item["id"],
            metadata=item.get("metadata") or {},
            report=item.get("report") or {},
        )
        for item in reports
    ]


@router.get("/memory/search", response_model=SearchResponse)
async def search_memory(
    q: str = Query(..., min_length=1, description="Semantic search query"),
    n: int = Query(3, ge=1, le=10),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> SearchResponse:
    owner_key = _owner_or_empty(user, x_guest_id)
    if not owner_key:
        return SearchResponse(query=q, results=[])
    try:
        results = chroma_store.search_similar(q, n_results=n, owner_key=owner_key)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Semantic search failed: {exc}",
        ) from exc

    items = [
        StoredReportItem(
            id=item["id"],
            metadata=item.get("metadata") or {},
            report=item.get("report") or {},
            distance=item.get("distance"),
        )
        for item in results
    ]
    return SearchResponse(query=q, results=items)


@router.get("/memory/tickers", response_model=List[str])
async def list_memory_tickers(
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> List[str]:
    owner_key = _owner_or_empty(user, x_guest_id)
    if not owner_key:
        return []
    try:
        return chroma_store.list_all_tickers(owner_key=owner_key)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list tickers: {exc}",
        ) from exc

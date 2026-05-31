from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.memory import chroma_store, session_store

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


@router.get("/memory/history", response_model=List[SessionHistoryEntry])
async def get_memory_history() -> List[SessionHistoryEntry]:
    history = session_store.get_session_history()
    return [SessionHistoryEntry.model_validate(entry) for entry in history]


@router.get("/memory/reports/{ticker}", response_model=List[StoredReportItem])
async def get_reports_for_ticker(ticker: str) -> List[StoredReportItem]:
    symbol = ticker.upper().strip()
    try:
        reports = chroma_store.get_by_ticker(symbol)
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
) -> SearchResponse:
    try:
        results = chroma_store.search_similar(q, n_results=n)
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
async def list_memory_tickers() -> List[str]:
    try:
        return chroma_store.list_all_tickers()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list tickers: {exc}",
        ) from exc

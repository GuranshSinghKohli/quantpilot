from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import RecommendationsResponse
from app.routers.watchlist import _load_watchlist
from app.services.recommendation_scanner import scan_recommendations

router = APIRouter(tags=["recommendations"])


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_investment_recommendations(
    tickers: Optional[str] = Query(
        None,
        description="Comma-separated tickers to scan (max 12). Defaults to popular names + your watchlist.",
    ),
    limit: int = Query(5, ge=1, le=8),
) -> RecommendationsResponse:
    symbols: List[str] = []

    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        try:
            for entry in _load_watchlist():
                sym = entry.get("ticker", "").upper()
                if sym:
                    symbols.append(sym)
        except Exception:
            pass

    try:
        return await scan_recommendations(
            tickers=symbols if symbols else None,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Recommendation scan failed: {exc}",
        ) from exc

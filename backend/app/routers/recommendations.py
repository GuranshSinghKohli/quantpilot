from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_optional_user
from app.db.models import User
from app.db.session import get_db
from app.models.schemas import RecommendationsResponse
from app.services import watchlist_store
from app.services.recommendation_scanner import scan_recommendations

router = APIRouter(tags=["recommendations"])


@router.get("/recommendations", response_model=RecommendationsResponse)
async def get_investment_recommendations(
    tickers: Optional[str] = Query(
        None,
        description="Comma-separated tickers to scan (max 12). Defaults to popular names + your watchlist.",
    ),
    limit: int = Query(5, ge=1, le=8),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> RecommendationsResponse:
    symbols: List[str] = []

    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        try:
            for entry in watchlist_store.list_watchlist(db, user):
                if entry.ticker:
                    symbols.append(entry.ticker.upper())
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

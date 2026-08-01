import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.portfolio_sync_agent import extract_positions
from app.auth.deps import get_current_user
from app.db.models import User
from app.db.session import get_db, get_or_create_default_portfolio
from app.jobs.briefing_job import generate_briefing_for_user
from app.mcp_client import MCPClientError, call_tool
from app.models.agent_schemas import DailyBriefingResponse
from app.models.auth_schemas import PortfolioSummary, WatchlistEntry
from app.models.portfolio_sync_schemas import (
    PortfolioSyncConfirmRequest,
    PortfolioSyncOutput,
    PortfolioSyncPreviewRequest,
)
from app.observability.logger import get_logger, log_event
from app.services import briefing_store, watchlist_store

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
logger = get_logger("portfolio_router")


@router.get("", response_model=PortfolioSummary)
def get_my_portfolio(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortfolioSummary:
    portfolio = get_or_create_default_portfolio(db, user)
    holdings = watchlist_store.list_watchlist(db, user)
    return PortfolioSummary(
        id=portfolio.id,
        name=portfolio.name,
        holdings_count=len(holdings),
        holdings=holdings,
    )


@router.post("/holdings/{ticker}", response_model=WatchlistEntry)
def add_portfolio_holding(
    ticker: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WatchlistEntry:
    return watchlist_store.add_holding(db, ticker, user=user)


@router.delete("/holdings/{ticker}")
def remove_portfolio_holding(
    ticker: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return watchlist_store.remove_holding(db, ticker, user=user)


@router.get("/briefing", response_model=DailyBriefingResponse)
def get_latest_briefing(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DailyBriefingResponse:
    row = briefing_store.get_latest_briefing(db, user)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No briefing yet. Generate one or wait for the daily job.",
        )
    return briefing_store.briefing_to_response(row)


@router.get("/briefings", response_model=List[DailyBriefingResponse])
def list_my_briefings(
    limit: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[DailyBriefingResponse]:
    rows = briefing_store.list_briefings(db, user, limit=limit)
    return [briefing_store.briefing_to_response(r) for r in rows]


@router.post("/briefing/generate", response_model=DailyBriefingResponse)
async def generate_briefing_now(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DailyBriefingResponse:
    return await generate_briefing_for_user(db, user)


@router.post("/sync/preview", response_model=PortfolioSyncOutput)
async def preview_portfolio_sync(
    body: PortfolioSyncPreviewRequest,
    user: User = Depends(get_current_user),
) -> PortfolioSyncOutput:
    """Extract positions from pasted text or an OpenClaw browser snapshot.

    Nothing is saved here — this is a preview the user must confirm via
    POST /portfolio/sync/confirm. We never handle broker credentials: text
    comes from either a manual paste or a read-only snapshot of the user's
    own already-signed-in browser tab.
    """
    raw_text = (body.raw_text or "").strip()
    source = "paste"

    if body.use_openclaw:
        source = "openclaw"
        try:
            snap = await call_tool("snapshot_active_browser_tab", {})
        except MCPClientError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"OpenClaw snapshot unavailable: {exc}",
            ) from exc
        if not snap.get("enabled", True):
            raise HTTPException(status_code=400, detail=snap.get("error") or "OpenClaw not configured.")
        if snap.get("error"):
            raise HTTPException(status_code=502, detail=snap["error"])
        raw_text = snap.get("text") or ""

    if not raw_text:
        raise HTTPException(
            status_code=400,
            detail="No page text to extract from. Paste your positions page text.",
        )

    result = await extract_positions(raw_text, source=source)
    log_event(
        logger,
        logging.INFO,
        "Portfolio sync preview",
        user_id=user.id,
        source=source,
        positions_found=len(result.positions),
        confidence=result.confidence_score,
    )
    return result


@router.post("/sync/confirm", response_model=PortfolioSummary)
def confirm_portfolio_sync(
    body: PortfolioSyncConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PortfolioSummary:
    """Persist user-confirmed positions from a sync preview into holdings."""
    if not body.positions:
        raise HTTPException(status_code=400, detail="No positions to save.")

    watchlist_store.upsert_synced_positions(
        db,
        [p.model_dump() for p in body.positions],
        user=user,
    )
    portfolio = get_or_create_default_portfolio(db, user)
    holdings = watchlist_store.list_watchlist(db, user)
    log_event(
        logger,
        logging.INFO,
        "Portfolio sync confirmed",
        user_id=user.id,
        count=len(body.positions),
    )
    return PortfolioSummary(
        id=portfolio.id,
        name=portfolio.name,
        holdings_count=len(holdings),
        holdings=holdings,
    )

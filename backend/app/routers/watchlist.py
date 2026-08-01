from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_optional_user
from app.db.models import User
from app.db.session import get_db
from app.models.auth_schemas import WatchlistAddBody, WatchlistEntry
from app.services import watchlist_store

router = APIRouter(tags=["watchlist"])


@router.get("/watchlist", response_model=List[WatchlistEntry])
def get_watchlist(
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> List[WatchlistEntry]:
    return watchlist_store.list_watchlist(db, user)


@router.post("/watchlist/{ticker}", response_model=WatchlistEntry)
def add_to_watchlist(
    ticker: str,
    body: Optional[WatchlistAddBody] = Body(default=None),
    notes: str = Query(default="", max_length=500),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> WatchlistEntry:
    note_text = body.notes if body is not None else notes
    return watchlist_store.add_holding(db, ticker, notes=note_text, user=user)


@router.delete("/watchlist/{ticker}")
def remove_from_watchlist(
    ticker: str,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> Dict[str, str]:
    return watchlist_store.remove_holding(db, ticker, user=user)

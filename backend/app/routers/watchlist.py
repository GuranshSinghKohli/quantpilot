import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "watchlist.json"


class WatchlistEntry(BaseModel):
    ticker: str
    added_at: str
    notes: str = ""


class WatchlistAddBody(BaseModel):
    notes: str = Field(default="", max_length=500)


def _load_watchlist() -> List[Dict[str, Any]]:
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_PATH.exists():
        WATCHLIST_PATH.write_text("[]", encoding="utf-8")
        return []
    with WATCHLIST_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_watchlist(entries: List[Dict[str, Any]]) -> None:
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with WATCHLIST_PATH.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


router = APIRouter(tags=["watchlist"])


@router.get("/watchlist", response_model=List[WatchlistEntry])
async def get_watchlist() -> List[WatchlistEntry]:
    entries = _load_watchlist()
    return [WatchlistEntry.model_validate(e) for e in entries]


@router.post("/watchlist/{ticker}", response_model=WatchlistEntry)
async def add_to_watchlist(
    ticker: str,
    body: Optional[WatchlistAddBody] = Body(default=None),
    notes: str = Query(default="", max_length=500),
) -> WatchlistEntry:
    symbol = ticker.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker symbol is required.")

    note_text = body.notes if body is not None else notes

    entries = _load_watchlist()
    for entry in entries:
        if entry.get("ticker", "").upper() == symbol:
            return WatchlistEntry.model_validate(entry)

    new_entry = {
        "ticker": symbol,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "notes": note_text,
    }
    entries.append(new_entry)
    _save_watchlist(entries)
    return WatchlistEntry.model_validate(new_entry)


@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str) -> Dict[str, str]:
    symbol = ticker.upper().strip()
    entries = _load_watchlist()
    updated = [e for e in entries if e.get("ticker", "").upper() != symbol]

    if len(updated) == len(entries):
        raise HTTPException(status_code=404, detail=f"{symbol} not in watchlist.")

    _save_watchlist(updated)
    return {"status": "removed", "ticker": symbol}

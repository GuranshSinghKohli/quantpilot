"""Per-user / per-guest-device analysis history (replaces global session_store)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, User

_GUEST_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_MAX_HISTORY = 40


def normalize_guest_key(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value or not _GUEST_KEY_RE.match(value):
        return None
    return value


def resolve_owner_key(
    user: Optional[User],
    guest_key: Optional[str] = None,
) -> Optional[str]:
    """Return a stable owner scope key, or None when no identity is available."""
    if user is not None:
        return f"user:{user.id}"
    normalized = normalize_guest_key(guest_key)
    if normalized:
        return f"guest:{normalized}"
    return None


def add_run(
    db: Session,
    *,
    owner_key: str,
    user: Optional[User],
    ticker: str,
    recommendation: str = "",
    risk_level: str = "",
    chroma_doc_id: Optional[str] = None,
) -> Dict[str, Any]:
    symbol = ticker.upper().strip()
    run = AnalysisRun(
        owner_key=owner_key,
        user_id=user.id if user is not None else None,
        ticker=symbol,
        recommendation=(recommendation or "")[:512],
        risk_level=(risk_level or "")[:32],
        chroma_doc_id=chroma_doc_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Cap per-owner history length.
    older = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.owner_key == owner_key)
        .order_by(AnalysisRun.created_at.desc())
        .offset(_MAX_HISTORY)
    ).all()
    if older:
        for row in older:
            db.delete(row)
        db.commit()

    return _to_entry(run)


def list_recent(
    db: Session,
    owner_key: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    rows = db.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.owner_key == owner_key)
        .order_by(AnalysisRun.created_at.desc())
        .limit(max(1, min(limit, _MAX_HISTORY)))
    ).all()
    return [_to_entry(row) for row in rows]


def _to_entry(run: AnalysisRun) -> Dict[str, Any]:
    created = run.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "ticker": run.ticker,
        "timestamp": created.isoformat() if created else datetime.now(timezone.utc).isoformat(),
        "recommendation": run.recommendation or "",
        "risk_level": run.risk_level or "",
    }

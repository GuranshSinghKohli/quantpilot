"""Persist and load daily portfolio briefings."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Briefing, Portfolio, User
from app.db.session import get_or_create_default_portfolio
from app.models.agent_schemas import (
    DailyBriefingResponse,
    PortfolioAnalysis,
    PortfolioBriefingOutput,
    PortfolioHolding,
)


def _parse_list(raw: str) -> List:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def briefing_to_response(row: Briefing) -> DailyBriefingResponse:
    holdings_raw = _parse_list(row.holdings_snapshot_json)
    holdings: List[PortfolioHolding] = []
    for item in holdings_raw:
        if isinstance(item, dict):
            try:
                holdings.append(PortfolioHolding.model_validate(item))
            except Exception:
                continue

    generated = row.generated_at
    if generated is not None and generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    return DailyBriefingResponse(
        id=row.id,
        generated_at=generated.isoformat() if generated else datetime.now(timezone.utc).isoformat(),
        headline=row.headline or "",
        summary=row.summary or "",
        highlights=[str(x) for x in _parse_list(row.highlights_json)],
        risks=[str(x) for x in _parse_list(row.risks_json)],
        watch_tickers=[str(x).upper() for x in _parse_list(row.watch_tickers_json)],
        holdings_snapshot=holdings,
        confidence_score=float(row.confidence_score or 0.5),
        status=row.status or "ok",
        error_message=row.error_message or "",
    )


def save_briefing(
    db: Session,
    *,
    user: User,
    portfolio: Portfolio,
    output: PortfolioBriefingOutput,
    analysis: PortfolioAnalysis,
    status: str = "ok",
) -> Briefing:
    row = Briefing(
        user_id=user.id,
        portfolio_id=portfolio.id,
        generated_at=datetime.now(timezone.utc),
        headline=output.headline or "",
        summary=output.summary or "",
        highlights_json=json.dumps(output.highlights or []),
        risks_json=json.dumps(output.risks or []),
        watch_tickers_json=json.dumps(output.watch_tickers or []),
        holdings_snapshot_json=json.dumps([h.model_dump() for h in analysis.holdings]),
        confidence_score=float(output.confidence_score or 0.5),
        status=status if not output.error_message else "error",
        error_message=output.error_message or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_latest_briefing(db: Session, user: User) -> Optional[Briefing]:
    return db.scalar(
        select(Briefing)
        .where(Briefing.user_id == user.id)
        .order_by(Briefing.generated_at.desc())
        .limit(1)
    )


def list_briefings(db: Session, user: User, limit: int = 7) -> List[Briefing]:
    return list(
        db.scalars(
            select(Briefing)
            .where(Briefing.user_id == user.id)
            .order_by(Briefing.generated_at.desc())
            .limit(limit)
        ).all()
    )


def ensure_portfolio(db: Session, user: User) -> Portfolio:
    return get_or_create_default_portfolio(db, user)

"""Phase 10 — compose existing ledger/alert/memory data for the desk UI.

Does not invent prices, activity, or conclusions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AlertEvent, Investigation, User
from app.models.dashboard_schemas import (
    ActivityItem,
    CompanyIntelligenceResponse,
    DashboardCounts,
    DashboardResponse,
    PortfolioSnapshot,
)
from app.services import alert_store, company_memory, evidence_ledger_store, watchlist_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _start_of_utc_day(now: Optional[datetime] = None) -> datetime:
    stamp = now or _now()
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0)


def build_dashboard(
    db: Session,
    *,
    owner_key: str,
    user: Optional[User],
) -> DashboardResponse:
    cases = evidence_ledger_store.list_investigations(db, owner_key, limit=40)
    today = _start_of_utc_day()
    active_status = {"planning", "collecting", "verifying"}
    skipped_status = {"skipped_market_noise"}
    today_cases = [
        c
        for c in cases
        if c.created_at
        and (
            c.created_at
            if c.created_at.tzinfo
            else c.created_at.replace(tzinfo=timezone.utc)
        )
        >= today
    ]
    active = [c for c in cases if c.status in active_status]
    completed_today = [
        c
        for c in today_cases
        if c.status == "complete"
    ]
    skipped = [c for c in cases if c.status in skipped_status]
    alerts: List[Any] = []
    unread = 0
    alerts_today = 0
    if user is not None:
        rows = alert_store.list_events(db, user, unread_only=False, limit=20)
        alerts = [alert_store.event_to_out(e) for e in rows]
        unread = alert_store.unread_count(db, user)
        alerts_today = sum(
            1
            for e in rows
            if e.created_at
            and (
                e.created_at
                if e.created_at.tzinfo
                else e.created_at.replace(tzinfo=timezone.utc)
            )
            >= today
        )

    portfolio = _portfolio_snapshot(db, user)
    activity = _activity_from_records(db, owner_key, user)
    company_updates = _company_updates(db, owner_key, cases[:8])
    notes = [
        "Counts and activity are derived from stored investigations and alerts.",
        "No placeholder market values are invented.",
    ]
    return DashboardResponse(
        generated_at=_iso(_now()),
        counts=DashboardCounts(
            investigations_today=len(today_cases),
            active=len(active),
            completed_today=len(completed_today),
            skipped=len(skipped),
            alerts_unread=unread,
            alerts_today=alerts_today,
        ),
        portfolio=portfolio,
        active=active[:8],
        recent=cases[:12],
        alerts=alerts[:8],
        activity=activity[:24],
        company_updates=company_updates,
        notes=notes,
    )


def build_company_intelligence(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
) -> CompanyIntelligenceResponse:
    symbol = ticker.upper().strip()
    memory = company_memory.build_summary(db, owner_key=owner_key, ticker=symbol)
    ctx = company_memory.retrieve_for_investigation(
        db, owner_key=owner_key, ticker=symbol
    )
    cases = [
        c
        for c in evidence_ledger_store.list_investigations(db, owner_key, limit=40)
        if c.ticker.upper() == symbol
    ]
    items = company_memory._load_active(db, owner_key, symbol, include_outdated=True)
    timeline = [
        company_memory._to_out(i)
        for i in sorted(
            items,
            key=lambda row: row.observed_at or row.created_at,
            reverse=True,
        )
        if i.status != "ARCHIVED"
    ][:24]
    notes = list(memory.notes or [])
    if not cases and not timeline:
        notes.append("Not enough historical intelligence yet.")
    return CompanyIntelligenceResponse(
        ticker=symbol,
        memory=memory,
        investigations=cases[:12],
        timeline=timeline,
        related_investigation_ids=ctx.related_investigation_ids,
        management_trend=memory.management_trend or ctx.management_trend,
        notes=notes,
    )


def set_alert_feedback(
    db: Session,
    user: User,
    event_id: int,
    feedback: str,
) -> Optional[AlertEvent]:
    allowed = {"", "useful", "not_useful"}
    if feedback not in allowed:
        return None
    event = db.scalar(
        select(AlertEvent).where(
            AlertEvent.id == event_id,
            AlertEvent.user_id == user.id,
        )
    )
    if event is None:
        return None
    event.feedback = feedback
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _portfolio_snapshot(db: Session, user: Optional[User]) -> PortfolioSnapshot:
    if user is None:
        return PortfolioSnapshot(note="Sign in to attach holdings to this desk.")
    entries = watchlist_store.list_watchlist(db, user)
    tickers = [e.ticker.upper() for e in entries if e.ticker]
    valued = 0.0
    has_value = False
    weights: List[tuple] = []
    for entry in entries:
        shares = getattr(entry, "shares", None)
        cost = getattr(entry, "avg_cost", None)
        if shares and cost:
            notional = float(shares) * float(cost)
            valued += notional
            has_value = True
            weights.append((entry.ticker.upper(), notional))
    largest_ticker = None
    largest_weight_pct = None
    if has_value and valued > 0 and weights:
        weights.sort(key=lambda item: item[1], reverse=True)
        largest_ticker = weights[0][0]
        largest_weight_pct = round(weights[0][1] / valued * 100.0, 1)
    note = (
        "Cost-basis snapshot from stored positions. Live marks are not invented."
        if has_value
        else "Holdings listed without inventing a market value."
    )
    return PortfolioSnapshot(
        holdings_count=len(tickers),
        tickers=tickers[:16],
        total_market_value=round(valued, 2) if has_value else None,
        weighted_by_real_positions=has_value,
        largest_ticker=largest_ticker,
        largest_weight_pct=largest_weight_pct,
        note=note,
    )


def _activity_from_records(
    db: Session,
    owner_key: str,
    user: Optional[User],
) -> List[ActivityItem]:
    items: List[ActivityItem] = []
    rows = db.scalars(
        select(Investigation)
        .where(Investigation.owner_key == owner_key)
        .order_by(Investigation.created_at.desc())
        .limit(12)
    ).all()
    for row in rows:
        items.append(
            ActivityItem(
                at=_iso(row.created_at),
                ticker=row.ticker,
                kind="investigation_opened",
                title=f"{row.ticker} — investigation opened",
                detail=row.summary or row.trigger_reason or "",
                investigation_id=row.id,
                source="investigations",
            )
        )
        if row.status == "collecting":
            items.append(
                ActivityItem(
                    at=_iso(row.created_at),
                    ticker=row.ticker,
                    kind="collecting",
                    title=f"{row.ticker} — evidence collection",
                    detail=row.summary or "",
                    investigation_id=row.id,
                )
            )
        if row.status in {"verifying", "complete"}:
            items.append(
                ActivityItem(
                    at=_iso(row.completed_at or row.created_at),
                    ticker=row.ticker,
                    kind="verifying" if row.status == "verifying" else "completed",
                    title=(
                        f"{row.ticker} — verification"
                        if row.status == "verifying"
                        else f"{row.ticker} — investigation completed"
                    ),
                    detail=row.summary or "",
                    investigation_id=row.id,
                )
            )
        if row.status == "skipped_market_noise":
            items.append(
                ActivityItem(
                    at=_iso(row.completed_at or row.created_at),
                    ticker=row.ticker,
                    kind="skipped",
                    title=f"{row.ticker} — skipped as market noise",
                    detail=row.summary or "",
                    investigation_id=row.id,
                )
            )
        roster = _roster(row)
        graph = roster.get("investigation_graph") if isinstance(roster, dict) else {}
        if isinstance(graph, dict) and graph.get("status"):
            items.append(
                ActivityItem(
                    at=_iso(row.completed_at or row.created_at),
                    ticker=row.ticker,
                    kind="graph",
                    title=f"{row.ticker} — LangGraph {graph.get('status')}",
                    detail=str((graph.get("metrics") or {}) or "")[:180],
                    investigation_id=row.id,
                    source="investigation_graph",
                )
            )
    if user is not None:
        for ev in alert_store.list_events(db, user, limit=12):
            items.append(
                ActivityItem(
                    at=_iso(ev.created_at),
                    ticker=ev.ticker,
                    kind="alert",
                    title=ev.title or f"{ev.ticker} alert",
                    detail=ev.message or "",
                    investigation_id=ev.investigation_id,
                    source="alerts",
                )
            )
    items.sort(key=lambda i: i.at or "", reverse=True)
    return items


def _company_updates(
    db: Session,
    owner_key: str,
    cases: List[Any],
) -> List[Dict[str, Any]]:
    seen = []
    out: List[Dict[str, Any]] = []
    for case in cases:
        symbol = case.ticker.upper()
        if symbol in seen:
            continue
        seen.append(symbol)
        summary = company_memory.build_summary(db, owner_key=owner_key, ticker=symbol)
        if not summary.recent_themes and not summary.recurring_risks and not summary.management_trend:
            continue
        out.append(
            {
                "ticker": symbol,
                "themes": summary.recent_themes[:4],
                "risks": summary.recurring_risks[:3],
                "management_trend": summary.management_trend,
                "last_updated": summary.last_updated,
            }
        )
    return out[:6]


def _roster(row: Investigation) -> Dict[str, Any]:
    try:
        raw = json.loads(row.roster_json or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}

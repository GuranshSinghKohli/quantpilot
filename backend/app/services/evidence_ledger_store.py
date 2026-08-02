"""PRD v3 Phase 7 — Evidence Ledger persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import (
    Claim,
    ClaimEvidenceLink,
    EvidenceItem,
    Investigation,
    User,
)
from app.models.investigation_schemas import (
    ClaimCreateRequest,
    ClaimEvidenceLinkOut,
    ClaimOut,
    EvidenceCreateRequest,
    EvidenceItemOut,
    InvestigationCreateRequest,
    InvestigationDetail,
    InvestigationSummary,
)
from app.services import analysis_history_store


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_investigation(
    db: Session,
    *,
    owner_key: str,
    user: Optional[User],
    body: InvestigationCreateRequest,
) -> InvestigationDetail:
    symbol = body.ticker.upper().strip()
    row = Investigation(
        owner_key=owner_key,
        user_id=user.id if user is not None else None,
        ticker=symbol,
        trigger_reason=body.trigger_reason,
        status="planning",
        move_pct=body.move_pct,
        window_label=body.window_label or "",
        summary=body.summary
        or f"Investigation opened for {symbol}. Running move detection and evidence plan…",
        created_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return get_investigation(db, owner_key=owner_key, investigation_id=row.id)


def list_investigations(
    db: Session,
    owner_key: str,
    *,
    limit: int = 20,
) -> List[InvestigationSummary]:
    rows = db.scalars(
        select(Investigation)
        .where(Investigation.owner_key == owner_key)
        .options(
            selectinload(Investigation.claims),
            selectinload(Investigation.evidence_items),
        )
        .order_by(Investigation.created_at.desc())
        .limit(max(1, min(limit, 50)))
    ).all()
    return [_to_summary(r) for r in rows]


def get_investigation(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
) -> Optional[InvestigationDetail]:
    row = db.scalar(
        select(Investigation)
        .where(
            Investigation.id == investigation_id,
            Investigation.owner_key == owner_key,
        )
        .options(
            selectinload(Investigation.claims).selectinload(Claim.evidence_links).selectinload(
                ClaimEvidenceLink.evidence
            ),
            selectinload(Investigation.evidence_items),
        )
    )
    if row is None:
        return None
    return _to_detail(row)


def add_evidence(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    body: EvidenceCreateRequest,
) -> Optional[EvidenceItemOut]:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return None
    item = EvidenceItem(
        investigation_id=inv.id,
        source_type=(body.source_type or "other")[:32],
        retrieval_method=(body.retrieval_method or "user")[:32],
        title=(body.title or "")[:280],
        excerpt=body.excerpt or "",
        source_url=(body.source_url or "")[:1024],
        raw_payload_json="{}",
        created_at=_now(),
    )
    db.add(item)
    if inv.status == "planning":
        inv.status = "collecting"
    db.commit()
    db.refresh(item)
    return EvidenceItemOut.model_validate(item)


def add_claim(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    body: ClaimCreateRequest,
) -> Optional[ClaimOut]:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return None

    claim = Claim(
        investigation_id=inv.id,
        statement=body.statement,
        stance=body.stance,
        confidence_score=body.confidence_score,
        rank=body.rank,
        devil_advocate_notes=body.devil_advocate_notes or "",
        created_at=_now(),
    )
    db.add(claim)
    db.flush()

    if body.evidence_ids:
        owned_ids = {
            e.id
            for e in db.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.investigation_id == inv.id,
                    EvidenceItem.id.in_(body.evidence_ids),
                )
            ).all()
        }
        for eid in body.evidence_ids:
            if eid not in owned_ids:
                continue
            db.add(
                ClaimEvidenceLink(
                    claim_id=claim.id,
                    evidence_id=eid,
                    relation="supports",
                    created_at=_now(),
                )
            )

    if inv.status in ("planning", "collecting"):
        inv.status = "verifying"
    db.commit()
    detail = get_investigation(db, owner_key=owner_key, investigation_id=inv.id)
    if detail is None:
        return None
    for c in detail.claims:
        if c.id == claim.id:
            return c
    return ClaimOut.model_validate(claim)


def link_claim_evidence(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    claim_id: int,
    evidence_id: int,
    relation: str = "supports",
    note: str = "",
) -> Optional[ClaimEvidenceLinkOut]:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return None
    claim = db.scalar(
        select(Claim).where(Claim.id == claim_id, Claim.investigation_id == inv.id)
    )
    evidence = db.scalar(
        select(EvidenceItem).where(
            EvidenceItem.id == evidence_id,
            EvidenceItem.investigation_id == inv.id,
        )
    )
    if claim is None or evidence is None:
        return None

    existing = db.scalar(
        select(ClaimEvidenceLink).where(
            ClaimEvidenceLink.claim_id == claim_id,
            ClaimEvidenceLink.evidence_id == evidence_id,
        )
    )
    if existing is not None:
        existing.relation = relation
        existing.note = (note or "")[:280]
        db.commit()
        db.refresh(existing)
        return ClaimEvidenceLinkOut(
            id=existing.id,
            evidence_id=existing.evidence_id,
            relation=existing.relation,
            note=existing.note,
            evidence=EvidenceItemOut.model_validate(evidence),
        )

    link = ClaimEvidenceLink(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation=relation,
        note=(note or "")[:280],
        created_at=_now(),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return ClaimEvidenceLinkOut(
        id=link.id,
        evidence_id=link.evidence_id,
        relation=link.relation,
        note=link.note,
        evidence=EvidenceItemOut.model_validate(evidence),
    )


def mark_complete(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    summary: str = "",
) -> Optional[InvestigationDetail]:
    return mark_status(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        status="complete",
        summary=summary,
        complete=True,
    )


def set_status(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    status: str,
    summary: str = "",
) -> Optional[InvestigationDetail]:
    return mark_status(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        status=status,
        summary=summary,
        complete=False,
    )


def mark_status(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    status: str,
    summary: str = "",
    complete: bool = False,
    error_message: str = "",
) -> Optional[InvestigationDetail]:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return None
    inv.status = status
    if summary:
        inv.summary = summary
    if error_message:
        inv.error_message = error_message
    if complete or status in ("complete", "failed", "skipped_market_noise"):
        inv.completed_at = _now()
    db.commit()
    detail = get_investigation(db, owner_key=owner_key, investigation_id=investigation_id)
    # FR-10 — best-effort semantic index when a case settles.
    if status in ("complete", "skipped_market_noise"):
        try:
            from app.services import investigation_search

            investigation_search.index_investigation(
                db, owner_key=owner_key, investigation_id=investigation_id
            )
        except Exception:
            pass
    return detail


def mark_failed(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    error_message: str,
) -> Optional[InvestigationDetail]:
    return mark_status(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        status="failed",
        summary="Investigation failed during Phase 8 pipeline.",
        error_message=(error_message or "")[:2000],
        complete=True,
    )


def update_move_metadata(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    move_pct: Optional[float],
    window_label: str,
    summary: str = "",
) -> Optional[InvestigationDetail]:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return None
    inv.move_pct = move_pct
    inv.window_label = (window_label or "")[:64]
    if summary:
        inv.summary = summary
    db.commit()
    return get_investigation(db, owner_key=owner_key, investigation_id=investigation_id)


def replace_system_evidence(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    items: List[Dict[str, Any]],
) -> List[int]:
    """Replace auto-collected evidence (keeps user-added rows). Returns new evidence ids."""
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return []

    existing = db.scalars(
        select(EvidenceItem).where(EvidenceItem.investigation_id == inv.id)
    ).all()
    for row in existing:
        if row.retrieval_method in ("mcp", "system", "openclaw", "httpx"):
            db.delete(row)
    db.flush()

    ids: List[int] = []
    for item in items:
        row = EvidenceItem(
            investigation_id=inv.id,
            source_type=str(item.get("source_type") or "other")[:32],
            retrieval_method=str(item.get("retrieval_method") or "system")[:32],
            title=str(item.get("title") or "")[:280],
            excerpt=str(item.get("excerpt") or ""),
            source_url=str(item.get("source_url") or "")[:1024],
            raw_payload_json=json.dumps(item.get("raw_payload") or {}, default=str),
            created_at=_now(),
        )
        db.add(row)
        db.flush()
        ids.append(row.id)

    if inv.status == "planning":
        inv.status = "collecting"
    db.commit()
    return ids


def replace_system_claims(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    hypotheses: List[Dict[str, Any]],
    evidence_ids: List[int],
) -> None:
    """Replace auto-ranked claims and re-link to evidence indices."""
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return

    existing = db.scalars(select(Claim).where(Claim.investigation_id == inv.id)).all()
    for claim in existing:
        # Phase 8 overwrites machine-generated claims; keep user claims that have
        # empty devil notes and stance unknown only if retrieval wasn't system — for
        # simplicity replace all claims on a run (investigation is machine-owned).
        db.delete(claim)
    db.flush()

    for hypo in hypotheses:
        # Prefer hypothesis weight for the score shown in the ledger UI.
        score = hypo.get("weight")
        if score is None:
            score = hypo.get("confidence_score") or 0.0
        claim = Claim(
            investigation_id=inv.id,
            statement=str(hypo.get("statement") or "")[:4000],
            stance=str(hypo.get("stance") or "unknown")[:32],
            confidence_score=float(score),
            rank=int(hypo.get("rank") or 0),
            devil_advocate_notes=str(hypo.get("devil_advocate_notes") or ""),
            created_at=_now(),
        )
        db.add(claim)
        db.flush()

        for idx in hypo.get("evidence_indices") or []:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if i < 0 or i >= len(evidence_ids):
                continue
            db.add(
                ClaimEvidenceLink(
                    claim_id=claim.id,
                    evidence_id=evidence_ids[i],
                    relation="supports",
                    note="",
                    created_at=_now(),
                )
            )

    if inv.status in ("planning", "collecting"):
        inv.status = "verifying"
    db.commit()


def resolve_owner(user: Optional[User], guest_key: Optional[str]) -> Optional[str]:
    return analysis_history_store.resolve_owner_key(user, guest_key)


def recent_investigation_exists(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
    within_hours: float = 6.0,
) -> bool:
    """Cooldown helper for scheduled sweeps."""
    symbol = ticker.upper().strip()
    cutoff = _now() - timedelta(hours=max(0.1, within_hours))
    row = db.scalar(
        select(Investigation.id)
        .where(
            Investigation.owner_key == owner_key,
            Investigation.ticker == symbol,
            Investigation.created_at >= cutoff,
        )
        .limit(1)
    )
    return row is not None


def _owned_investigation(
    db: Session, owner_key: str, investigation_id: int
) -> Optional[Investigation]:
    return db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id,
            Investigation.owner_key == owner_key,
        )
    )


def _to_summary(row: Investigation) -> InvestigationSummary:
    return InvestigationSummary(
        id=row.id,
        ticker=row.ticker,
        trigger_reason=row.trigger_reason,
        status=row.status,
        move_pct=row.move_pct,
        window_label=row.window_label or "",
        summary=row.summary or "",
        created_at=row.created_at,
        completed_at=row.completed_at,
        claims_count=len(row.claims) if row.claims is not None else 0,
        evidence_count=len(row.evidence_items) if row.evidence_items is not None else 0,
    )


def set_verification_audit(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    verification_notes: str = "",
    da_outcome: Optional[Dict[str, Any]] = None,
) -> None:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None:
        return
    if hasattr(inv, "verification_notes"):
        inv.verification_notes = (verification_notes or "")[:8000]
    if hasattr(inv, "da_outcome_json"):
        inv.da_outcome_json = json.dumps(da_outcome or {}, default=str)
    db.commit()


def set_roster_context(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    roster: Optional[Dict[str, Any]] = None,
) -> None:
    inv = _owned_investigation(db, owner_key, investigation_id)
    if inv is None or not hasattr(inv, "roster_json"):
        return
    inv.roster_json = json.dumps(roster or {}, default=str)
    db.commit()


def _to_detail(row: Investigation) -> InvestigationDetail:
    from app.models.investigation_schemas import DevilsAdvocateOutcome

    claims: List[ClaimOut] = []
    for claim in row.claims or []:
        links = [
            ClaimEvidenceLinkOut(
                id=link.id,
                evidence_id=link.evidence_id,
                relation=link.relation,
                note=link.note or "",
                evidence=(
                    EvidenceItemOut.model_validate(link.evidence)
                    if link.evidence is not None
                    else None
                ),
            )
            for link in (claim.evidence_links or [])
        ]
        claims.append(
            ClaimOut(
                id=claim.id,
                statement=claim.statement,
                stance=claim.stance,
                confidence_score=claim.confidence_score,
                rank=claim.rank,
                devil_advocate_notes=claim.devil_advocate_notes or "",
                evidence_links=links,
                created_at=claim.created_at,
            )
        )
    evidence = [EvidenceItemOut.model_validate(e) for e in (row.evidence_items or [])]
    da_raw: Dict[str, Any] = {}
    raw_json = getattr(row, "da_outcome_json", None) or "{}"
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, dict):
            da_raw = parsed
    except Exception:
        da_raw = {}
    da_outcome = None
    if da_raw:
        try:
            da_outcome = DevilsAdvocateOutcome.model_validate(da_raw)
        except Exception:
            da_outcome = DevilsAdvocateOutcome(
                outcome=str(da_raw.get("outcome") or "held"),
                counterargument=str(da_raw.get("counterargument") or ""),
                leading_weakened=bool(da_raw.get("leading_weakened")),
                reversal=bool(da_raw.get("reversal")),
            )

    roster = None
    roster_raw = getattr(row, "roster_json", None) or "{}"
    try:
        roster_parsed = json.loads(roster_raw)
        if isinstance(roster_parsed, dict) and roster_parsed:
            from app.models.investigation_schemas import InvestigationRosterContext

            roster = InvestigationRosterContext.model_validate(roster_parsed)
    except Exception:
        roster = None

    return InvestigationDetail(
        id=row.id,
        ticker=row.ticker,
        trigger_reason=row.trigger_reason,
        status=row.status,
        move_pct=row.move_pct,
        window_label=row.window_label or "",
        summary=row.summary or "",
        error_message=row.error_message or "",
        verification_notes=getattr(row, "verification_notes", "") or "",
        da_outcome=da_outcome,
        roster=roster,
        created_at=row.created_at,
        completed_at=row.completed_at,
        claims_count=len(claims),
        evidence_count=len(evidence),
        claims=claims,
        evidence_items=evidence,
    )

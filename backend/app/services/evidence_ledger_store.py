"""PRD v3 Phase 7 — Evidence Ledger persistence helpers."""

from __future__ import annotations

import json
import logging
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
from app.observability.logger import get_logger, log_event
from app.services import analysis_history_store, evidence_graph

logger = get_logger("evidence_ledger")


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


def delete_investigation(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
) -> bool:
    """Remove a case and cascaded claims/evidence. Returns False if not found."""
    row = db.scalar(
        select(Investigation).where(
            Investigation.id == investigation_id,
            Investigation.owner_key == owner_key,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    try:
        from app.memory import investigation_chroma

        investigation_chroma.delete_investigation(investigation_id)
    except Exception:
        pass
    return True


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
    enriched = evidence_graph.enrich_item(
        {
            "source_type": body.source_type or "other",
            "retrieval_method": body.retrieval_method or "user",
            "title": body.title or "",
            "excerpt": body.excerpt or "",
            "source_url": body.source_url or "",
            "raw_payload": {},
        }
    )
    hashed = str(enriched.get("content_hash") or "")
    if hashed:
        existing_dup = db.scalar(
            select(EvidenceItem).where(
                EvidenceItem.investigation_id == inv.id,
                EvidenceItem.content_hash == hashed,
                EvidenceItem.source_url == (body.source_url or "")[:1024],
            )
        )
        if existing_dup is not None and getattr(existing_dup, "content_hash", ""):
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_DEDUPLICATED",
                event="EVIDENCE_DEDUPLICATED",
                investigation_id=inv.id,
                evidence_id=existing_dup.id,
            )
            return _evidence_out(existing_dup)
    item = EvidenceItem(
        investigation_id=inv.id,
        source_type=(body.source_type or "other")[:32],
        retrieval_method=(enriched.get("retrieval_method") or "user")[:32],
        title=(body.title or "")[:280],
        excerpt=body.excerpt or "",
        source_url=(body.source_url or "")[:1024],
        raw_payload_json=json.dumps(enriched.get("raw_payload") or {}, default=str),
        created_at=_now(),
    )
    if hasattr(item, "content_hash"):
        item.content_hash = hashed
    db.add(item)
    log_event(
        logger,
        logging.INFO,
        "EVIDENCE_CREATED",
        event="EVIDENCE_CREATED",
        investigation_id=inv.id,
        source_type=item.source_type,
    )
    if inv.status == "planning":
        inv.status = "collecting"
    db.commit()
    db.refresh(item)
    return _evidence_out(item)


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
    if hasattr(claim, "claim_type"):
        claim.claim_type = evidence_graph.infer_claim_type(body.statement, rank=body.rank)
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
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_LINKED",
                event="EVIDENCE_LINKED",
                investigation_id=inv.id,
                claim_id=claim.id,
                evidence_id=eid,
                relationship_type="supports",
            )
    log_event(
        logger,
        logging.INFO,
        "CLAIM_CREATED",
        event="CLAIM_CREATED",
        investigation_id=inv.id,
        claim_id=claim.id,
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
            evidence=_evidence_out(evidence),
        )

    link = ClaimEvidenceLink(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation=relation,
        note=(note or "")[:280],
        created_at=_now(),
    )
    db.add(link)
    log_event(
        logger,
        logging.INFO,
        "EVIDENCE_LINKED",
        event="EVIDENCE_LINKED",
        investigation_id=inv.id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        relationship_type=relation,
    )
    db.commit()
    db.refresh(link)
    return ClaimEvidenceLinkOut(
        id=link.id,
        evidence_id=link.evidence_id,
        relation=link.relation,
        note=link.note,
        evidence=_evidence_out(evidence),
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
        if row.retrieval_method in ("mcp", "system", "openclaw", "httpx", "browser_mcp"):
            db.delete(row)
    db.flush()

    kept, _dropped = evidence_graph.dedupe_items(
        items, investigation_id=inv.id
    )
    ids: List[int] = []
    for item in kept:
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
        if hasattr(row, "content_hash"):
            row.content_hash = str(item.get("content_hash") or "")[:64]
        db.add(row)
        db.flush()
        ids.append(row.id)
        log_event(
            logger,
            logging.INFO,
            "EVIDENCE_CREATED",
            event="EVIDENCE_CREATED",
            investigation_id=inv.id,
            evidence_id=row.id,
            source_type=row.source_type,
        )

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

    evidence_dicts = _evidence_dicts_for_ids(db, evidence_ids)
    for hypo in hypotheses:
        # Prefer hypothesis weight for the score shown in the ledger UI.
        score = hypo.get("weight")
        if score is None:
            score = hypo.get("confidence_score") or 0.0
        statement = str(hypo.get("statement") or "")[:4000]
        linked_idxs = list(
            dict.fromkeys(
                list(hypo.get("supporting_indices") or [])
                + list(hypo.get("contradicting_indices") or [])
                + list(hypo.get("evidence_indices") or [])
            )
        )
        ok, reason = evidence_graph.validate_citations(
            statement,
            evidence_dicts,
            linked_idxs,
            investigation_id=inv.id,
        )
        notes = str(hypo.get("devil_advocate_notes") or "")
        if not ok:
            statement = evidence_graph.strip_unknown_citations(
                statement, evidence_dicts
            )[:4000]
            notes = f"{notes} {reason}".strip()
        claim = Claim(
            investigation_id=inv.id,
            statement=statement,
            stance=str(hypo.get("stance") or "unknown")[:32],
            confidence_score=float(score),
            rank=int(hypo.get("rank") or 0),
            devil_advocate_notes=notes,
            created_at=_now(),
        )
        if hasattr(claim, "claim_type"):
            claim.claim_type = evidence_graph.infer_claim_type(
                statement, rank=int(hypo.get("rank") or 0)
            )
        db.add(claim)
        db.flush()
        log_event(
            logger,
            logging.INFO,
            "CLAIM_CREATED",
            event="CLAIM_CREATED",
            investigation_id=inv.id,
            claim_id=claim.id,
        )

        linked = set()
        pairs = []
        for idx in hypo.get("contradicting_indices") or []:
            pairs.append((idx, "contradicts"))
        for idx in hypo.get("supporting_indices") or []:
            pairs.append((idx, "supports"))
        for idx in hypo.get("neutral_indices") or []:
            pairs.append((idx, "context"))
        if not pairs:
            for idx in hypo.get("evidence_indices") or []:
                pairs.append((idx, "supports"))
        for idx, relation in pairs:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if i < 0 or i >= len(evidence_ids):
                log_event(
                    logger,
                    logging.WARNING,
                    "EVIDENCE_VALIDATION_FAILED",
                    event="EVIDENCE_VALIDATION_FAILED",
                    investigation_id=inv.id,
                    claim_id=claim.id,
                    reason="invalid_evidence_index",
                )
                continue
            if i in linked:
                continue
            linked.add(i)
            db.add(
                ClaimEvidenceLink(
                    claim_id=claim.id,
                    evidence_id=evidence_ids[i],
                    relation=relation,
                    note="",
                    created_at=_now(),
                )
            )
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_LINKED",
                event="EVIDENCE_LINKED",
                investigation_id=inv.id,
                claim_id=claim.id,
                evidence_id=evidence_ids[i],
                relationship_type=relation,
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


def latest_investigation(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
) -> Optional[Investigation]:
    """Most recent case for this owner+ticker, any status."""
    symbol = ticker.upper().strip()
    return db.scalar(
        select(Investigation)
        .where(
            Investigation.owner_key == owner_key,
            Investigation.ticker == symbol,
        )
        .order_by(Investigation.created_at.desc())
        .limit(1)
    )


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


def _payload(row: EvidenceItem) -> Dict[str, Any]:
    try:
        parsed = json.loads(getattr(row, "raw_payload_json", None) or "{}")
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return {}
    return {}


def _evidence_dicts_for_ids(db: Session, evidence_ids: List[int]) -> List[Dict[str, Any]]:
    if not evidence_ids:
        return []
    rows = db.scalars(
        select(EvidenceItem).where(EvidenceItem.id.in_(evidence_ids))
    ).all()
    by_id = {row.id: row for row in rows}
    out: List[Dict[str, Any]] = []
    for eid in evidence_ids:
        row = by_id.get(eid)
        if row is None:
            out.append({})
            continue
        payload = {}
        try:
            parsed = json.loads(row.raw_payload_json or "{}")
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
        out.append(
            {
                "source_type": row.source_type,
                "retrieval_method": row.retrieval_method,
                "title": row.title,
                "excerpt": row.excerpt,
                "source_url": row.source_url,
                "raw_payload": payload,
                "published_at": (payload.get("provenance") or {}).get("published_at"),
            }
        )
    return out


def _evidence_out(row: EvidenceItem) -> EvidenceItemOut:
    payload: Dict[str, Any] = {}
    try:
        parsed = json.loads(getattr(row, "raw_payload_json", None) or "{}")
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = {}
    prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    published = None
    retrieved = None
    try:
        if prov.get("published_at"):
            published = datetime.fromisoformat(str(prov["published_at"]).replace("Z", "+00:00"))
    except ValueError:
        published = None
    try:
        if prov.get("retrieved_at"):
            retrieved = datetime.fromisoformat(str(prov["retrieved_at"]).replace("Z", "+00:00"))
    except ValueError:
        retrieved = None
    return EvidenceItemOut(
        id=row.id,
        source_type=row.source_type,
        retrieval_method=row.retrieval_method,
        title=row.title,
        excerpt=row.excerpt or "",
        source_url=row.source_url or "",
        created_at=row.created_at,
        source_name=str(prov.get("source_name") or ""),
        published_at=published,
        retrieved_at=retrieved or row.created_at,
        content_hash=str(
            getattr(row, "content_hash", "") or prov.get("content_hash") or ""
        ),
        source_quality=str(prov.get("source_quality") or ""),
        source_class=str(prov.get("source_class") or ""),
        source_taxonomy=str(prov.get("source_taxonomy") or ""),
        temporal_relevance=str(prov.get("temporal_relevance") or ""),
        source_group=str(prov.get("source_group") or ""),
        source_tier=str(prov.get("source_tier") or ""),
    )


def _to_detail(row: Investigation) -> InvestigationDetail:
    from app.models.investigation_schemas import DevilsAdvocateOutcome

    evidence_rows = list(row.evidence_items or [])
    evidence_dicts = [
        {
            "source_type": e.source_type,
            "retrieval_method": e.retrieval_method,
            "title": e.title,
            "excerpt": e.excerpt,
            "source_url": e.source_url,
            "raw_payload": _payload(e),
        }
        for e in evidence_rows
    ]
    index_by_id = {e.id: i for i, e in enumerate(evidence_rows)}
    claims: List[ClaimOut] = []
    assessments = []
    for claim in row.claims or []:
        links = [
            ClaimEvidenceLinkOut(
                id=link.id,
                evidence_id=link.evidence_id,
                relation=link.relation,
                note=link.note or "",
                evidence=(
                    _evidence_out(link.evidence)
                    if link.evidence is not None
                    else None
                ),
            )
            for link in (claim.evidence_links or [])
        ]
        supporting = [
            index_by_id[link.evidence_id]
            for link in (claim.evidence_links or [])
            if link.relation == "supports" and link.evidence_id in index_by_id
        ]
        contradicting = [
            index_by_id[link.evidence_id]
            for link in (claim.evidence_links or [])
            if link.relation == "contradicts" and link.evidence_id in index_by_id
        ]
        context = [
            index_by_id[link.evidence_id]
            for link in (claim.evidence_links or [])
            if link.relation in {"context", "neutral"} and link.evidence_id in index_by_id
        ]
        assessment = evidence_graph.assess_claim(
            claim.statement,
            evidence_dicts,
            supporting_indices=supporting,
            contradicting_indices=contradicting,
            context_indices=context,
            rank=claim.rank,
            claim_type=getattr(claim, "claim_type", None) or None,
        )
        ok, reason = evidence_graph.validate_citations(
            claim.statement,
            evidence_dicts,
            supporting + contradicting + context,
            investigation_id=row.id,
            claim_id=claim.id,
        )
        if not ok:
            assessment.status = "UNSUPPORTED"
            assessment.notes.append(reason)
            assessment.gaps.append(reason)
        assessments.append(assessment)
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
                claim_type=getattr(claim, "claim_type", None)
                or assessment.claim_type,
                support_status=assessment.status,
                support_assessment=assessment.model_dump(),
            )
        )
    evidence = [_evidence_out(e) for e in evidence_rows]
    snapshot = evidence_graph.summarize_investigation(evidence_dicts, assessments)
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
        evidence_summary=snapshot.model_dump(),
    )

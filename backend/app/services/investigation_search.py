"""PRD v3 FR-10 — natural-language search over investigations and evidence."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Claim, EvidenceItem, Investigation
from app.observability.logger import get_logger, log_event

logger = get_logger("investigation_search")

_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "why",
    "did",
    "does",
    "what",
    "when",
    "with",
    "from",
    "about",
    "this",
    "that",
    "was",
    "were",
    "is",
    "are",
    "it",
    "its",
    "my",
    "me",
    "how",
    "move",
    "moved",
}


def tokenize_query(query: str) -> List[str]:
    raw = re.findall(r"[a-z0-9\.\+\-/%$]+", (query or "").lower())
    tokens: List[str] = []
    for t in raw:
        t = t.strip(".-/%$")
        if len(t) < 2 or t in _STOPWORDS:
            continue
        tokens.append(t)
    # Keep original order, unique
    seen: Set[str] = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def search_investigations(
    db: Session,
    *,
    owner_key: str,
    query: str,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Search owner's ledger with keyword scoring; optionally blend Chroma semantic hits.
    Works offline without OpenAI (keyword path).
    """
    q = (query or "").strip()
    tokens = tokenize_query(q)
    if not q:
        return {"query": q, "mode": "empty", "results": []}

    keyword_hits = _keyword_search(db, owner_key=owner_key, query=q, tokens=tokens)
    mode = "keyword"
    semantic_ids: List[int] = []
    if os.getenv("OPENAI_API_KEY", "").strip():
        try:
            from app.memory import investigation_chroma

            semantic_ids = investigation_chroma.search_investigation_ids(
                q, owner_key=owner_key, n_results=max(limit, 5)
            )
            if semantic_ids:
                mode = "hybrid"
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "Investigation semantic search unavailable",
                error=str(exc),
            )

    by_id: Dict[int, Dict[str, Any]] = {h["investigation_id"]: h for h in keyword_hits}
    for rank, inv_id in enumerate(semantic_ids):
        if inv_id in by_id:
            by_id[inv_id]["score"] = float(by_id[inv_id]["score"]) + max(0.5, 2.0 - rank * 0.15)
            by_id[inv_id]["match_sources"] = sorted(
                set(by_id[inv_id].get("match_sources") or []) | {"semantic"}
            )
        else:
            detail = _hydrate_hit(db, owner_key=owner_key, investigation_id=inv_id, snippet="")
            if detail:
                detail["score"] = max(0.4, 1.8 - rank * 0.15)
                detail["match_sources"] = ["semantic"]
                by_id[inv_id] = detail

    ranked = sorted(by_id.values(), key=lambda x: (-float(x["score"]), -int(x["investigation_id"])))
    return {"query": q, "mode": mode, "results": ranked[: max(1, min(25, limit))]}


def index_investigation(db: Session, *, owner_key: str, investigation_id: int) -> None:
    """Best-effort embed of a completed investigation for semantic search."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return
    try:
        from app.memory import investigation_chroma

        inv = db.scalar(
            select(Investigation)
            .options(
                selectinload(Investigation.claims),
                selectinload(Investigation.evidence_items),
            )
            .where(
                Investigation.id == investigation_id,
                Investigation.owner_key == owner_key,
            )
        )
        if inv is None:
            return
        document = _document_for_index(inv)
        investigation_chroma.upsert_investigation(
            owner_key=owner_key,
            investigation_id=inv.id,
            ticker=inv.ticker,
            document=document,
            metadata={
                "status": inv.status,
                "trigger_reason": inv.trigger_reason,
                "summary": (inv.summary or "")[:280],
            },
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Investigation index failed",
            investigation_id=investigation_id,
            error=str(exc),
        )


def _keyword_search(
    db: Session,
    *,
    owner_key: str,
    query: str,
    tokens: List[str],
) -> List[Dict[str, Any]]:
    # Broad SQL prefilter using OR of token likes; score in Python.
    patterns = tokens or [t for t in [query.lower().strip()] if t]
    if not patterns:
        return []
    inv_filters = [Investigation.owner_key == owner_key]
    like_clauses = []
    for tok in patterns[:8]:
        like = f"%{tok}%"
        like_clauses.append(Investigation.ticker.ilike(like))
        like_clauses.append(Investigation.summary.ilike(like))
        like_clauses.append(Investigation.verification_notes.ilike(like))
    claim_likes = [Claim.statement.ilike(f"%{tok}%") for tok in patterns[:8]]
    evidence_likes = [
        or_(
            EvidenceItem.title.ilike(f"%{tok}%"),
            EvidenceItem.excerpt.ilike(f"%{tok}%"),
        )
        for tok in patterns[:8]
    ]
    claim_ids = (
        select(Claim.investigation_id)
        .join(Investigation, Investigation.id == Claim.investigation_id)
        .where(Investigation.owner_key == owner_key, or_(*claim_likes))
    )
    evidence_ids = (
        select(EvidenceItem.investigation_id)
        .join(Investigation, Investigation.id == EvidenceItem.investigation_id)
        .where(Investigation.owner_key == owner_key, or_(*evidence_likes))
    )
    stmt = (
        select(Investigation)
        .options(
            selectinload(Investigation.claims),
            selectinload(Investigation.evidence_items),
        )
        .where(
            *inv_filters,
            or_(
                *like_clauses,
                Investigation.id.in_(claim_ids),
                Investigation.id.in_(evidence_ids),
            ),
        )
        .order_by(Investigation.created_at.desc())
        .limit(80)
    )
    rows = list(db.scalars(stmt).unique().all())
    hits: List[Dict[str, Any]] = []
    for inv in rows:
        score, snippet, sources = _score_investigation(inv, query=query, tokens=tokens)
        if score <= 0:
            continue
        hits.append(
            {
                "investigation_id": inv.id,
                "ticker": inv.ticker,
                "status": inv.status,
                "trigger_reason": inv.trigger_reason,
                "summary": (inv.summary or "")[:400],
                "snippet": snippet[:320],
                "score": round(score, 3),
                "match_sources": sorted(sources),
                "move_pct": inv.move_pct,
                "created_at": inv.created_at.isoformat() if inv.created_at else "",
                "claims_count": len(inv.claims or []),
                "evidence_count": len(inv.evidence_items or []),
            }
        )
    hits.sort(key=lambda x: (-float(x["score"]), -int(x["investigation_id"])))
    return hits


def _score_investigation(
    inv: Investigation,
    *,
    query: str,
    tokens: List[str],
) -> Tuple[float, str, Set[str]]:
    q_lower = query.lower()
    score = 0.0
    sources: Set[str] = set()
    best_snippet = inv.summary or ""

    ticker = (inv.ticker or "").lower()
    if ticker and (ticker == q_lower.strip("$") or ticker in tokens):
        score += 4.0
        sources.add("ticker")
        best_snippet = f"${inv.ticker} investigation"

    blob_summary = f"{inv.summary or ''} {inv.verification_notes or ''}".lower()
    for tok in tokens:
        if tok in blob_summary:
            score += 1.2
            sources.add("summary")
    if q_lower and q_lower in blob_summary:
        score += 1.5
        sources.add("summary")
        best_snippet = inv.summary or best_snippet

    for claim in inv.claims or []:
        text = (claim.statement or "").lower()
        hit = False
        for tok in tokens:
            if tok in text:
                score += 1.6
                hit = True
        if q_lower and q_lower in text:
            score += 1.0
            hit = True
        if hit:
            sources.add("claim")
            best_snippet = claim.statement or best_snippet

    for ev in inv.evidence_items or []:
        text = f"{ev.title or ''} {ev.excerpt or ''}".lower()
        hit = False
        for tok in tokens:
            if tok in text:
                score += 1.3
                hit = True
        if q_lower and q_lower in text:
            score += 0.8
            hit = True
        if hit:
            sources.add("evidence")
            best_snippet = (ev.title or ev.excerpt or best_snippet)[:320]

    return score, best_snippet, sources


def _hydrate_hit(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    snippet: str,
) -> Optional[Dict[str, Any]]:
    inv = db.scalar(
        select(Investigation)
        .options(
            selectinload(Investigation.claims),
            selectinload(Investigation.evidence_items),
        )
        .where(
            Investigation.id == investigation_id,
            Investigation.owner_key == owner_key,
        )
    )
    if inv is None:
        return None
    return {
        "investigation_id": inv.id,
        "ticker": inv.ticker,
        "status": inv.status,
        "trigger_reason": inv.trigger_reason,
        "summary": (inv.summary or "")[:400],
        "snippet": (snippet or inv.summary or "")[:320],
        "score": 0.0,
        "match_sources": [],
        "move_pct": inv.move_pct,
        "created_at": inv.created_at.isoformat() if inv.created_at else "",
        "claims_count": len(inv.claims or []),
        "evidence_count": len(inv.evidence_items or []),
    }


def _document_for_index(inv: Investigation) -> str:
    parts = [
        f"Ticker: {inv.ticker}",
        f"Status: {inv.status}",
        f"Trigger: {inv.trigger_reason}",
        f"Summary: {inv.summary or ''}",
        f"Verification: {inv.verification_notes or ''}",
    ]
    for claim in (inv.claims or [])[:8]:
        parts.append(f"Claim: {claim.statement}")
    for ev in (inv.evidence_items or [])[:10]:
        parts.append(f"Evidence ({ev.source_type}): {ev.title}. {ev.excerpt[:400]}")
    return "\n".join(parts)[:12000]

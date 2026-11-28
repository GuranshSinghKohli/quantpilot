"""Phase 8 — company-level longitudinal memory on top of the Evidence Ledger.

Structured store: PostgreSQL/SQLite `company_memory_items`.
Semantic retrieval: existing Chroma `investigation_ledger` (no second vector DB).
Current investigation evidence always wins over stale memory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CompanyMemoryItem, Investigation
from app.models.company_memory_schemas import (
    CompanyMemoryContext,
    CompanyMemoryItemOut,
    CompanyMemorySummary,
    SimilarEventOut,
)
from app.observability.logger import get_logger, log_event
from app.services import evidence_graph

logger = get_logger("company_memory")

MEMORY_TYPES = {
    "COMPANY_FACT",
    "MANAGEMENT_STATEMENT",
    "RISK",
    "EVENT",
    "INVESTIGATION_CONCLUSION",
    "THEME",
}
_PORTFOLIO_RE = re.compile(
    r"\b(portfolio|holding|position size|% of (this )?user)\b", re.I
)
_GENERIC_RE = re.compile(
    r"^(the stock (moved|fell|rose)|insufficient evidence|market beta)\b",
    re.I,
)
_THEME_MAP = (
    (("export", "restriction", "china", "regulatory", "bis"), "export_restrictions"),
    (("margin", "gross margin"), "margins"),
    (("guidance", "outlook"), "guidance"),
    (("demand", "ai infrastructure", "customers are"), "ai_demand"),
    (("supply", "constraint", "shortage"), "supply_constraints"),
    (("capex", "capital expenditure"), "capex"),
    (("hiring", "headcount"), "hiring"),
    (("competitor", "competitive"), "competitive_pressure"),
    (("earnings", "revenue"), "earnings"),
)
_POS = re.compile(
    r"\b(strong|stronger|robust|raised|improve|improved|beat|solid|continued)\b",
    re.I,
)
_NEG = re.compile(
    r"\b(weak|weaker|cautious|selective|lowered|declined|cut|pressure|worsen)\b",
    re.I,
)


def retrieve_for_investigation(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
    event: Optional[Dict[str, Any]] = None,
    hypotheses: Optional[Sequence[str]] = None,
    move_pct: Optional[float] = None,
    investigation_id: int = 0,
    limit: int = 12,
) -> CompanyMemoryContext:
    """Relevant company memory only. Owner-scoped. No portfolio fields."""
    log_event(
        logger,
        logging.INFO,
        "MEMORY_RETRIEVAL_STARTED",
        event="MEMORY_RETRIEVAL_STARTED",
        ticker=ticker,
        investigation_id=investigation_id,
    )
    symbol = (ticker or "").upper().strip()
    items = _load_active(db, owner_key, symbol)
    ranked = _rank_relevant(items, event=event or {}, hypotheses=hypotheses or [])
    similar = find_similar_events(
        db,
        owner_key=owner_key,
        ticker=symbol,
        move_pct=move_pct,
        exclude_id=investigation_id,
    )
    related = [s.investigation_id for s in similar]
    related.extend(
        _recent_investigation_ids(db, owner_key, symbol, exclude_id=investigation_id)
    )
    trend = detect_management_trend(items)
    conflicts: List[str] = []
    payload = CompanyMemoryContext(
        ticker=symbol,
        summary=_compact_summary(symbol, ranked, trend, similar),
        items=[_to_out(i) for i in ranked[:limit]],
        similar_events=similar[:5],
        related_investigation_ids=list(dict.fromkeys(related))[:8],
        management_trend=trend,
        conflicts=conflicts,
    )
    log_event(
        logger,
        logging.INFO,
        "MEMORY_RETRIEVAL_COMPLETED",
        event="MEMORY_RETRIEVAL_COMPLETED",
        ticker=symbol,
        investigation_id=investigation_id,
        relevance=len(payload.items),
        status="ok",
    )
    return payload


def extract_and_store(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
    investigation_id: int,
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    evidence_ids: Optional[Sequence[int]] = None,
    move_pct: Optional[float] = None,
    occurred_at: Optional[datetime] = None,
) -> List[CompanyMemoryItem]:
    """Selective extract after an investigation. Source-backed only."""
    symbol = (ticker or "").upper().strip()
    when = occurred_at or datetime.now(timezone.utc)
    candidates = _extract_candidates(
        symbol,
        hypotheses,
        evidence,
        evidence_ids=list(evidence_ids or []),
        move_pct=move_pct,
        occurred_at=when,
        investigation_id=investigation_id,
    )
    stored: List[CompanyMemoryItem] = []
    for cand in candidates:
        if not _quality_ok(cand):
            continue
        item, action = upsert_memory(db, owner_key=owner_key, candidate=cand)
        if item is not None:
            stored.append(item)
            log_event(
                logger,
                logging.INFO,
                f"MEMORY_ITEM_{action}",
                event=f"MEMORY_ITEM_{action}",
                ticker=symbol,
                investigation_id=investigation_id,
                memory_id=item.id,
                memory_type=item.memory_type,
                status=item.status,
            )
    _maybe_upsert_theme(db, owner_key=owner_key, ticker=symbol, items=stored, when=when)
    log_event(
        logger,
        logging.INFO,
        "MEMORY_EXTRACTION_COMPLETED",
        event="MEMORY_EXTRACTION_COMPLETED",
        ticker=symbol,
        investigation_id=investigation_id,
        relevance=len(stored),
        status="ok",
    )
    return stored


def upsert_memory(
    db: Session,
    *,
    owner_key: str,
    candidate: Dict[str, Any],
) -> Tuple[Optional[CompanyMemoryItem], str]:
    """CREATE / CONFIRM / WEAKEN / CONTRADICT / ARCHIVE. Never silent overwrite."""
    symbol = str(candidate.get("ticker") or "").upper()
    mtype = str(candidate.get("memory_type") or "")
    statement = str(candidate.get("statement") or "").strip()
    if mtype not in MEMORY_TYPES or not statement:
        return None, "SKIPPED"
    theme = str(candidate.get("theme_key") or theme_key(statement))
    polarity = str(candidate.get("polarity") or polarity_of(statement))
    fingerprint = content_fingerprint(symbol, mtype, statement)
    observed = candidate.get("observed_at") or datetime.now(timezone.utc)
    gated = {
        **candidate,
        "ticker": symbol,
        "memory_type": mtype,
        "statement": statement,
        "observed_at": observed,
    }
    if not _quality_ok(gated):
        return None, "SKIPPED"

    existing = db.scalar(
        select(CompanyMemoryItem).where(
            CompanyMemoryItem.owner_key == owner_key,
            CompanyMemoryItem.ticker == symbol,
            CompanyMemoryItem.memory_type == mtype,
            CompanyMemoryItem.fingerprint == fingerprint,
        )
    )
    if existing is not None:
        existing.last_seen_at = observed
        existing.observation_count = int(existing.observation_count or 1) + 1
        if existing.status == "ACTIVE":
            existing.status = "CONFIRMED"
        extra = _extra(existing)
        dates = list(extra.get("observations") or [])
        dates.append(observed.isoformat() if hasattr(observed, "isoformat") else str(observed))
        extra["observations"] = dates[-12:]
        existing.extra_json = json.dumps(extra, default=str)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        log_event(
            logger,
            logging.INFO,
            "MEMORY_ITEM_DEDUPLICATED",
            event="MEMORY_ITEM_DEDUPLICATED",
            ticker=symbol,
            memory_id=existing.id,
            memory_type=mtype,
            status=existing.status,
        )
        return existing, "DEDUPLICATED"

    priors = _active_same_theme_rows(db, owner_key, symbol, mtype, theme)
    status = "ACTIVE"
    supersedes = None
    conflicting = [
        p
        for p in priors
        if p.polarity != polarity
        and polarity in {"POSITIVE", "NEGATIVE"}
        and p.polarity in {"POSITIVE", "NEGATIVE"}
    ]
    if conflicting:
        latest = conflicting[0]
        for prior in conflicting:
            prior.status = "OUTDATED"
            extra = _extra(prior)
            extra["changed_to"] = statement[:240]
            prior.extra_json = json.dumps(extra, default=str)
            db.add(prior)
        supersedes = latest.id
        status = "ACTIVE"
        log_event(
            logger,
            logging.INFO,
            "MEMORY_CONFLICT_DETECTED",
            event="MEMORY_CONFLICT_DETECTED",
            ticker=symbol,
            memory_id=latest.id,
            memory_type=mtype,
            status="OUTDATED",
        )

    item = CompanyMemoryItem(
        owner_key=owner_key,
        ticker=symbol,
        memory_type=mtype,
        statement=statement[:4000],
        theme_key=theme,
        status=status,
        polarity=polarity,
        fingerprint=fingerprint,
        observed_at=observed,
        last_seen_at=observed,
        observation_count=1,
        evidence_id=candidate.get("evidence_id"),
        investigation_id=candidate.get("investigation_id"),
        source_url=str(candidate.get("source_url") or "")[:1024],
        source_type=str(candidate.get("source_type") or "")[:32],
        supersedes_id=supersedes,
        extra_json=json.dumps(
            {"observations": [observed.isoformat() if hasattr(observed, "isoformat") else str(observed)]},
            default=str,
        ),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item, "CREATED"


def apply_status(
    db: Session,
    *,
    owner_key: str,
    memory_id: int,
    status: str,
) -> Optional[CompanyMemoryItem]:
    item = db.scalar(
        select(CompanyMemoryItem).where(
            CompanyMemoryItem.id == memory_id,
            CompanyMemoryItem.owner_key == owner_key,
        )
    )
    if item is None:
        return None
    if status not in {"ACTIVE", "CONFIRMED", "WEAKENED", "CONTRADICTED", "OUTDATED", "ARCHIVED"}:
        return item
    item.status = status
    db.add(item)
    db.commit()
    db.refresh(item)
    log_event(
        logger,
        logging.INFO,
        "MEMORY_ITEM_UPDATED",
        event="MEMORY_ITEM_UPDATED",
        ticker=item.ticker,
        memory_id=item.id,
        memory_type=item.memory_type,
        status=status,
    )
    return item


def find_similar_events(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
    move_pct: Optional[float],
    exclude_id: int = 0,
) -> List[SimilarEventOut]:
    if move_pct is None:
        return []
    rows = db.scalars(
        select(Investigation)
        .where(
            Investigation.owner_key == owner_key,
            Investigation.ticker == ticker.upper(),
            Investigation.status == "complete",
            Investigation.id != (exclude_id or 0),
        )
        .order_by(Investigation.completed_at.desc())
    ).all()
    out: List[SimilarEventOut] = []
    current = abs(float(move_pct))
    for row in rows:
        if row.move_pct is None:
            continue
        hist = abs(float(row.move_pct))
        if abs(hist - current) <= 3.0 and hist >= 2.0 and current >= 2.0:
            out.append(
                SimilarEventOut(
                    investigation_id=row.id,
                    ticker=row.ticker,
                    move_pct=row.move_pct,
                    summary=(row.summary or "")[:280],
                    completed_at=row.completed_at.isoformat() if row.completed_at else None,
                    similarity="similar",
                    note=(
                        f"Similar historical event: {row.ticker} "
                        f"{row.move_pct:+.1f}% (not the same event)."
                    ),
                )
            )
    return out[:5]


def detect_management_trend(items: Sequence[CompanyMemoryItem]) -> str:
    statements = [
        i
        for i in items
        if i.memory_type == "MANAGEMENT_STATEMENT" and i.theme_key == "ai_demand"
    ]
    if not statements:
        statements = [i for i in items if i.memory_type == "MANAGEMENT_STATEMENT"]
    if len(statements) < 2:
        return ""
    ordered = sorted(
        statements,
        key=lambda i: i.observed_at or i.created_at,
    )
    first, last = ordered[0], ordered[-1]
    if first.polarity == "POSITIVE" and last.polarity == "NEGATIVE":
        return "Demand commentary has weakened."
    if first.polarity == "NEGATIVE" and last.polarity == "POSITIVE":
        return "Demand commentary has strengthened."
    if last.theme_key == "margins" and last.polarity == "NEGATIVE":
        return "Margin commentary has weakened."
    if first.polarity == last.polarity:
        return "Management commentary on this theme is consistent with prior periods."
    return "Management commentary has changed; historical statements are preserved."


def build_summary(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
) -> CompanyMemorySummary:
    symbol = ticker.upper().strip()
    items = _load_active(db, owner_key, symbol, include_outdated=True)
    live = [i for i in items if i.status not in {"ARCHIVED"}]
    risks = [i for i in live if i.memory_type == "RISK" and i.status != "OUTDATED"]
    mgmt = [i for i in live if i.memory_type == "MANAGEMENT_STATEMENT"]
    events = [i for i in live if i.memory_type == "EVENT"]
    conclusions = [i for i in live if i.memory_type == "INVESTIGATION_CONCLUSION"]
    facts = [i for i in live if i.memory_type == "COMPANY_FACT"]
    themes = [i for i in live if i.memory_type == "THEME"]
    invs = _recent_investigation_ids(db, owner_key, symbol)
    inv_lines = []
    for inv_id in invs[:5]:
        row = db.scalar(
            select(Investigation).where(
                Investigation.id == inv_id,
                Investigation.owner_key == owner_key,
            )
        )
        if row is None:
            continue
        when = (row.completed_at or row.created_at).strftime("%Y-%m-%d")
        inv_lines.append(f"{when}: {(row.summary or row.trigger_reason or 'investigation')[:80]}")
    last = max((i.last_seen_at or i.created_at for i in live), default=None)
    return CompanyMemorySummary(
        ticker=symbol,
        last_updated=last.isoformat() if last else None,
        recent_themes=list(
            dict.fromkeys([i.theme_key for i in themes + risks + mgmt if i.theme_key])
        )[:8],
        recurring_risks=[i.statement for i in risks if i.observation_count >= 1][:6],
        recent_investigations=inv_lines,
        management_trend=detect_management_trend(items),
        key_facts=[i.statement for i in facts][:6],
        active_risks=[_to_out(i) for i in risks[:6]],
        management_themes=[_to_out(i) for i in mgmt[:8]],
        important_events=[_to_out(i) for i in events[:6]],
        key_claims=[_to_out(i) for i in conclusions[:6]],
        similar_events=[],
        notes=["Memory is source-backed and owner-scoped. Current evidence wins."],
    )


def theme_key(text: str) -> str:
    blob = (text or "").lower()
    for tokens, key in _THEME_MAP:
        if any(tok in blob for tok in tokens):
            return key
    return "general"


def polarity_of(text: str) -> str:
    pos = bool(_POS.search(text or ""))
    neg = bool(_NEG.search(text or ""))
    if pos and not neg:
        return "POSITIVE"
    if neg and not pos:
        return "NEGATIVE"
    return "NEUTRAL"


def content_fingerprint(ticker: str, memory_type: str, statement: str) -> str:
    norm = re.sub(r"\s+", " ", (statement or "").lower()).strip()
    raw = f"{ticker.upper()}|{memory_type}|{norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_candidates(
    ticker: str,
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    evidence_ids: Sequence[int],
    move_pct: Optional[float],
    occurred_at: datetime,
    investigation_id: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if move_pct is not None:
        verb = "decline" if float(move_pct) < 0 else "rise"
        out.append(
            {
                "ticker": ticker,
                "memory_type": "EVENT",
                "statement": (
                    f"{ticker} experienced a {abs(float(move_pct)):.1f}% {verb} "
                    f"on {occurred_at.date().isoformat()}."
                ),
                "theme_key": "event",
                "polarity": "NEGATIVE" if float(move_pct) < 0 else "POSITIVE",
                "observed_at": occurred_at,
                "investigation_id": investigation_id,
                "source_type": "price",
            }
        )
    lead = hypotheses[0] if hypotheses else None
    if lead and (lead.get("evidence_indices") or lead.get("supporting_indices")):
        stmt = str(lead.get("statement") or "").strip()
        if stmt and not _GENERIC_RE.search(stmt):
            out.append(
                {
                    "ticker": ticker,
                    "memory_type": "INVESTIGATION_CONCLUSION",
                    "statement": stmt[:800],
                    "theme_key": theme_key(stmt),
                    "polarity": polarity_of(stmt),
                    "observed_at": occurred_at,
                    "investigation_id": investigation_id,
                    "evidence_id": _id_for_index(
                        lead.get("supporting_indices") or lead.get("evidence_indices"),
                        evidence_ids,
                    ),
                    "source_type": "claim",
                }
            )
    for hypo in hypotheses:
        kind = str(hypo.get("claim_type") or "")
        stmt = str(hypo.get("statement") or "").strip()
        if not stmt or not (hypo.get("evidence_indices") or hypo.get("supporting_indices")):
            continue
        eid = _id_for_index(
            hypo.get("supporting_indices") or hypo.get("evidence_indices"),
            evidence_ids,
        )
        if kind == "FACT" or (kind == "" and evidence_graph.infer_claim_type(stmt) == "FACT"):
            out.append(
                {
                    "ticker": ticker,
                    "memory_type": "COMPANY_FACT",
                    "statement": stmt[:800],
                    "theme_key": theme_key(stmt),
                    "observed_at": occurred_at,
                    "investigation_id": investigation_id,
                    "evidence_id": eid,
                    "source_type": "claim",
                }
            )
        if kind == "RISK" or theme_key(stmt) == "export_restrictions":
            if "risk" in stmt.lower() or "restriction" in stmt.lower() or "export" in stmt.lower():
                out.append(
                    {
                        "ticker": ticker,
                        "memory_type": "RISK",
                        "statement": stmt[:800],
                        "theme_key": theme_key(stmt) if theme_key(stmt) != "general" else "export_restrictions",
                        "polarity": "NEGATIVE",
                        "observed_at": occurred_at,
                        "investigation_id": investigation_id,
                        "evidence_id": eid,
                        "source_type": "claim",
                    }
                )
    for idx, item in enumerate(evidence):
        source = str(item.get("source_type") or "")
        if source not in {"transcript", "ir_page", "filing"}:
            continue
        if not evidence_graph._usable_evidence(item):
            continue
        body = f"{item.get('title') or ''} {item.get('excerpt') or ''}"
        for sent in _sentences(body):
            if not _looks_management(sent):
                continue
            eid = evidence_ids[idx] if idx < len(evidence_ids) else None
            out.append(
                {
                    "ticker": ticker,
                    "memory_type": "MANAGEMENT_STATEMENT",
                    "statement": sent[:800],
                    "theme_key": theme_key(sent),
                    "polarity": polarity_of(sent),
                    "observed_at": occurred_at,
                    "investigation_id": investigation_id,
                    "evidence_id": eid,
                    "source_url": str(item.get("source_url") or ""),
                    "source_type": source,
                }
            )
    return out[:16]


def _quality_ok(candidate: Dict[str, Any]) -> bool:
    statement = str(candidate.get("statement") or "").strip()
    if len(statement) < 12:
        return False
    if _PORTFOLIO_RE.search(statement):
        return False
    if _GENERIC_RE.search(statement):
        return False
    if candidate.get("memory_type") not in MEMORY_TYPES:
        return False
    if not candidate.get("investigation_id") and not candidate.get("evidence_id"):
        return False
    if not candidate.get("observed_at"):
        return False
    return True


def _load_active(
    db: Session,
    owner_key: str,
    ticker: str,
    *,
    include_outdated: bool = False,
) -> List[CompanyMemoryItem]:
    stmt = select(CompanyMemoryItem).where(
        CompanyMemoryItem.owner_key == owner_key,
        CompanyMemoryItem.ticker == ticker.upper(),
    )
    if not include_outdated:
        stmt = stmt.where(CompanyMemoryItem.status != "ARCHIVED")
    rows = list(db.scalars(stmt.order_by(CompanyMemoryItem.last_seen_at.desc())).all())
    return rows


def _rank_relevant(
    items: Sequence[CompanyMemoryItem],
    *,
    event: Dict[str, Any],
    hypotheses: Sequence[str],
) -> List[CompanyMemoryItem]:
    blob = " ".join(
        [str(event.get("event_type") or ""), str(event.get("ticker") or "")]
        + [str(h) for h in hypotheses]
    ).lower()
    scored: List[Tuple[int, CompanyMemoryItem]] = []
    for item in items:
        if item.status == "ARCHIVED":
            continue
        score = 1
        if item.theme_key and item.theme_key.replace("_", " ") in blob:
            score += 3
        if item.memory_type in {"RISK", "MANAGEMENT_STATEMENT", "THEME"}:
            score += 1
        if item.status == "OUTDATED":
            score -= 1
        scored.append((score, item))
    scored.sort(key=lambda x: (-x[0], x[1].last_seen_at or x[1].created_at), reverse=False)
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored]


def _recent_investigation_ids(
    db: Session,
    owner_key: str,
    ticker: str,
    *,
    exclude_id: int = 0,
) -> List[int]:
    rows = db.scalars(
        select(Investigation.id)
        .where(
            Investigation.owner_key == owner_key,
            Investigation.ticker == ticker.upper(),
            Investigation.status == "complete",
            Investigation.id != (exclude_id or 0),
        )
        .order_by(Investigation.completed_at.desc())
        .limit(8)
    ).all()
    return [int(i) for i in rows]


def _active_same_theme_rows(
    db: Session,
    owner_key: str,
    ticker: str,
    memory_type: str,
    theme: str,
) -> List[CompanyMemoryItem]:
    if not theme or theme in {"general", "event"}:
        return []
    return list(
        db.scalars(
            select(CompanyMemoryItem)
            .where(
                CompanyMemoryItem.owner_key == owner_key,
                CompanyMemoryItem.ticker == ticker,
                CompanyMemoryItem.memory_type == memory_type,
                CompanyMemoryItem.theme_key == theme,
                CompanyMemoryItem.status.in_(["ACTIVE", "CONFIRMED", "WEAKENED"]),
            )
            .order_by(
                CompanyMemoryItem.last_seen_at.desc(),
                CompanyMemoryItem.id.desc(),
            )
        ).all()
    )


def _active_same_theme(
    db: Session,
    owner_key: str,
    ticker: str,
    memory_type: str,
    theme: str,
) -> Optional[CompanyMemoryItem]:
    rows = _active_same_theme_rows(db, owner_key, ticker, memory_type, theme)
    return rows[0] if rows else None


def _maybe_upsert_theme(
    db: Session,
    *,
    owner_key: str,
    ticker: str,
    items: Sequence[CompanyMemoryItem],
    when: datetime,
) -> None:
    counts: Dict[str, int] = {}
    for item in _load_active(db, owner_key, ticker, include_outdated=True):
        if item.theme_key and item.theme_key not in {"general", "event"}:
            counts[item.theme_key] = counts.get(item.theme_key, 0) + int(item.observation_count or 1)
    for key, n in counts.items():
        if n < 2:
            continue
        inv_id = next((i.investigation_id for i in items if i.investigation_id), None)
        if not inv_id:
            continue
        upsert_memory(
            db,
            owner_key=owner_key,
            candidate={
                "ticker": ticker,
                "memory_type": "THEME",
                "statement": f"{key.replace('_', ' ')} has appeared across {n} memory observations.",
                "theme_key": key,
                "observed_at": when,
                "investigation_id": inv_id,
                "source_type": "derived",
            },
        )


def _compact_summary(
    ticker: str,
    items: Sequence[CompanyMemoryItem],
    trend: str,
    similar: Sequence[SimilarEventOut],
) -> str:
    parts = [f"{ticker} company memory."]
    risks = [i.statement for i in items if i.memory_type == "RISK"][:2]
    if risks:
        parts.append("Recurring risks: " + "; ".join(risks))
    if trend:
        parts.append(trend)
    if similar:
        parts.append(similar[0].note)
    parts.append("Current evidence takes precedence over historical memory.")
    return " ".join(parts)[:1200]


def _to_out(item: CompanyMemoryItem) -> CompanyMemoryItemOut:
    return CompanyMemoryItemOut(
        id=item.id,
        ticker=item.ticker,
        memory_type=item.memory_type,
        statement=item.statement,
        theme_key=item.theme_key,
        status=item.status,
        polarity=item.polarity,
        observed_at=item.observed_at.isoformat() if item.observed_at else None,
        last_seen_at=item.last_seen_at.isoformat() if item.last_seen_at else None,
        observation_count=item.observation_count,
        evidence_id=item.evidence_id,
        investigation_id=item.investigation_id,
        source_url=item.source_url or "",
        source_type=item.source_type or "",
        supersedes_id=item.supersedes_id,
        historical=True,
    )


def _extra(item: CompanyMemoryItem) -> Dict[str, Any]:
    try:
        raw = json.loads(item.extra_json or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _id_for_index(indices: Any, evidence_ids: Sequence[int]) -> Optional[int]:
    try:
        idx = int(list(indices or [])[0])
    except (TypeError, ValueError, IndexError):
        return None
    if 0 <= idx < len(evidence_ids):
        return int(evidence_ids[idx])
    return None


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return [p.strip() for p in parts if 20 <= len(p.strip()) <= 400][:8]


def _looks_management(sentence: str) -> bool:
    return bool(
        re.search(
            r"\b(management|we expect|expects|guidance|demand|customers|outlook)\b",
            sentence or "",
            re.I,
        )
    )


def historical_vs_current_note() -> str:
    return (
        "Company memory items are historical context. "
        "They are not current-event evidence and cannot support a new causal claim."
    )

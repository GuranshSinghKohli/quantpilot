"""Phase 5 — auditable evidence intelligence on the existing ledger.

Deterministic: hashing, grouping, quality, temporal labels, claim support.
Does not treat LLM text as evidence.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.models.evidence_graph_schemas import (
    ClaimSupportAssessment,
    ClaimSupportStatus,
    ClaimType,
    ConflictKind,
    EvidenceGraphSnapshot,
    EvidenceProvenance,
    EvidenceSummary,
    GraphTemporal,
    RelevanceLevel,
    SourceClass,
    SourceTaxonomy,
    SourceTier,
)
from app.observability.logger import get_logger, log_event
from app.services import hypothesis_engine

logger = get_logger("evidence_graph")

_URL_RE = re.compile(r"https?://[^\s)>\"]+", re.I)
_GUIDANCE_TERMS = ("guidance", "outlook", "margin")
_EARNINGS_TERMS = ("earnings", "revenue", "eps", "beat", "miss")
_REGULATORY_TERMS = ("export", "restriction", "ban", "sec", "regulatory")
_QUARTER_TERMS = ("quarter", "q1", "q2", "q3", "q4", "near-term", "next-quarter")
_ANNUAL_TERMS = ("annual", "full year", "full-year", "fy", "long-term")


def content_hash(title: str = "", excerpt: str = "") -> str:
    """SHA-256 of normalized excerpt, or title if excerpt is empty.

    Hashed: whitespace-collapsed lowercase body text.
    Not hashed: URL, timestamps, retrieval method, payload metadata.
    """
    body = _norm(excerpt) or _norm(title)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    cleaned = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=(parsed.netloc or "").lower().removeprefix("www."),
        path=(parsed.path or "").rstrip("/") or "/",
        query=urlencode(query),
        fragment="",
    )
    return urlunparse(cleaned)


def source_name(item: Dict[str, Any]) -> str:
    payload = item.get("raw_payload") or {}
    for key in ("publisher", "source", "provider", "company"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    host = (urlparse(str(item.get("source_url") or "")).hostname or "").replace(
        "www.", ""
    )
    if host:
        return host[:120]
    source = str(item.get("source_type") or "source")
    return source.replace("_", " ").title()


def source_taxonomy(item: Dict[str, Any]) -> SourceTaxonomy:
    source = str(item.get("source_type") or "").lower()
    return {
        "filing": "SEC_FILING",
        "ir_page": "PRIMARY_COMPANY",
        "transcript": "EARNINGS_TRANSCRIPT",
        "price": "MARKET_DATA",
        "macro": "ECONOMIC_DATA",
        "news": "FINANCIAL_NEWS",
        "options": "MARKET_DATA",
    }.get(source, "OTHER")


def source_class(item: Dict[str, Any], *, derived: bool = False) -> SourceClass:
    if derived:
        return "DERIVED"
    source = str(item.get("source_type") or "").lower()
    if source in {"filing", "ir_page", "transcript"}:
        return "PRIMARY"
    if source == "news":
        return "SECONDARY"
    if source in {"price", "options", "macro"}:
        return "MARKET"
    return "OTHER"


def source_tier(item: Dict[str, Any]) -> SourceTier:
    klass = source_class(item)
    quality = hypothesis_engine.source_quality(item)
    if klass == "PRIMARY" or quality == "PRIMARY":
        return "TIER_1"
    if quality == "HIGH" or str(item.get("source_type") or "") == "transcript":
        return "TIER_2"
    if quality == "MEDIUM":
        return "TIER_3"
    return "TIER_4"


def graph_temporal(
    item: Dict[str, Any], event_time: Optional[datetime]
) -> GraphTemporal:
    label = hypothesis_engine.temporal_label(item, event_time)
    return {
        "pre_event": "PRE_EVENT",
        "same_day": "EVENT_TIME",
        "post_event": "POST_EVENT",
        "retrospective": "POST_EVENT",
        "unknown": "UNKNOWN",
    }.get(label, "UNKNOWN")


def enrich_item(
    item: Dict[str, Any],
    *,
    event_time: Optional[datetime] = None,
    retrieved_at: Optional[datetime] = None,
    derived: bool = False,
    source_group: str = "",
) -> Dict[str, Any]:
    """Attach provenance to raw_payload. Does not invent source content."""
    clone = dict(item)
    payload = dict(clone.get("raw_payload") or {})
    existing_prov = (
        payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    )
    published = hypothesis_engine.extract_evidence_time(clone)
    retrieved = retrieved_at or datetime.now(timezone.utc)
    hashed = content_hash(str(clone.get("title") or ""), str(clone.get("excerpt") or ""))
    group = (
        source_group
        or existing_prov.get("source_group")
        or hypothesis_engine.independence_group(clone)
    )
    method = str(clone.get("retrieval_method") or "system")
    if method.lower() in {"browser", "browser_mcp"}:
        method = "browser_mcp"
        clone["retrieval_method"] = method
    temporal = graph_temporal(clone, event_time)
    if event_time is None and existing_prov.get("temporal_relevance") in {
        "PRE_EVENT",
        "EVENT_TIME",
        "POST_EVENT",
    }:
        temporal = existing_prov["temporal_relevance"]
    provenance = EvidenceProvenance(
        source_name=source_name(clone) or str(existing_prov.get("source_name") or ""),
        source_url=str(clone.get("source_url") or ""),
        source_type=str(clone.get("source_type") or "other"),
        source_taxonomy=source_taxonomy(clone),
        source_class=source_class(clone, derived=derived),
        source_tier=source_tier(clone),
        source_quality=hypothesis_engine.source_quality(clone),
        retrieval_method=method,
        published_at=(
            published.isoformat()
            if published
            else existing_prov.get("published_at")
        ),
        retrieved_at=str(existing_prov.get("retrieved_at") or retrieved.isoformat()),
        content_hash=hashed,
        source_group=group,
        temporal_relevance=temporal,
    )
    payload["provenance"] = provenance.model_dump()
    clone["raw_payload"] = payload
    clone["content_hash"] = hashed
    clone["source_group"] = group
    return clone


def dedupe_items(
    items: Sequence[Dict[str, Any]],
    *,
    investigation_id: int = 0,
    event_time: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Drop exact duplicate content at the same canonical URL.

    Same hash + different URL is kept and marked DERIVED in the same group.
    """
    kept: List[Dict[str, Any]] = []
    seen_keys: set = set()
    hash_groups: Dict[str, str] = {}
    dropped = 0
    for item in items:
        title = str(item.get("title") or "")
        excerpt = str(item.get("excerpt") or "")
        hashed = content_hash(title, excerpt)
        url = canonical_url(str(item.get("source_url") or ""))
        key = (hashed, url or _norm(title)[:80])
        if key in seen_keys:
            dropped += 1
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_DEDUPLICATED",
                event="EVIDENCE_DEDUPLICATED",
                investigation_id=investigation_id,
                source_type=item.get("source_type"),
                content_hash=hashed[:12],
            )
            continue
        seen_keys.add(key)
        derived = hashed in hash_groups
        group = hash_groups.get(hashed) or hypothesis_engine.independence_group(item)
        if hashed not in hash_groups:
            hash_groups[hashed] = group
        kept.append(
            enrich_item(
                item,
                derived=derived,
                source_group=group,
                event_time=event_time,
            )
        )
    return kept, dropped


def infer_claim_type(statement: str, *, rank: int = 0) -> ClaimType:
    text = (statement or "").lower()
    if any(token in text for token in ("caused", "contributed", "drove", "because", "likely")):
        return "CAUSAL_EXPLANATION"
    if any(token in text for token in ("risk", "could fall", "downside")):
        return "RISK"
    if re.search(r"\d+(\.\d+)?\s*%", text) or any(
        token in text
        for token in ("increased", "decreased", "rose", "fell", "was", "declined")
    ):
        if "may " in text or "suggest" in text:
            return "INTERPRETATION"
        return "FACT"
    if rank == 1 and "leading" in text:
        return "CONCLUSION"
    if any(
        token in text
        for token in ("may ", "might ", "appears", "suggest", "remained", "strong")
    ):
        return "INTERPRETATION"
    return "CAUSAL_EXPLANATION"


def relevance_for_claim(statement: str, item: Dict[str, Any]) -> RelevanceLevel:
    claim = (statement or "").lower()
    body = f"{item.get('title') or ''} {item.get('excerpt') or ''}".lower()
    source = str(item.get("source_type") or "")
    if source == "price" and any(token in claim for token in ("guid", "earn", "regulat", "competitor")):
        return "LOW"
    overlap = 0
    for family in (_GUIDANCE_TERMS, _EARNINGS_TERMS, _REGULATORY_TERMS):
        if any(t in claim for t in family) and any(t in body for t in family):
            overlap += 1
    if source in {"filing", "ir_page", "transcript"} and overlap:
        return "HIGH"
    if overlap:
        return "HIGH" if source != "price" else "LOW"
    if source == "news":
        return "MEDIUM"
    return "LOW"


def detect_conflict(
    supporting: Sequence[Dict[str, Any]],
    contradicting: Sequence[Dict[str, Any]],
) -> Tuple[ConflictKind, str]:
    if not supporting or not contradicting:
        return "NO_CONFLICT", ""
    support_text = " ".join(_item_text(i) for i in supporting)
    contra_text = " ".join(_item_text(i) for i in contradicting)
    if _has_any(support_text, _QUARTER_TERMS) and _has_any(contra_text, _ANNUAL_TERMS):
        return (
            "TEMPORAL_DIFFERENCE",
            "Near-term vs annual guidance may both be true.",
        )
    if _has_any(contra_text, _QUARTER_TERMS) and _has_any(support_text, _ANNUAL_TERMS):
        return (
            "TEMPORAL_DIFFERENCE",
            "Near-term vs annual guidance may both be true.",
        )
    opposite = (
        ("cut guidance", "guidance unchanged"),
        ("lowered outlook", "raised outlook"),
        ("miss", "beat"),
    )
    for left, right in opposite:
        if left in support_text and right in contra_text:
            return "DIRECT_CONTRADICTION", f"Sources disagree on '{left}' vs '{right}'."
        if right in support_text and left in contra_text:
            return "DIRECT_CONTRADICTION", f"Sources disagree on '{left}' vs '{right}'."
    return (
        "CONTEXTUAL_DIFFERENCE",
        "Supporting and contradicting items may refer to different facts.",
    )


def assess_claim(
    statement: str,
    evidence: Sequence[Dict[str, Any]],
    *,
    supporting_indices: Sequence[int] = (),
    contradicting_indices: Sequence[int] = (),
    context_indices: Sequence[int] = (),
    rank: int = 0,
    claim_type: Optional[ClaimType] = None,
) -> ClaimSupportAssessment:
    allowed = {
        "FACT",
        "INTERPRETATION",
        "CAUSAL_EXPLANATION",
        "RISK",
        "CONCLUSION",
    }
    kind: ClaimType
    if claim_type in allowed:
        kind = claim_type  # type: ignore[assignment]
    else:
        kind = infer_claim_type(statement, rank=rank)
    support_items = [
        item
        for item in (_at(evidence, i) for i in supporting_indices)
        if item and _usable_evidence(item)
    ]
    contra_items = [
        item
        for item in (_at(evidence, i) for i in contradicting_indices)
        if item and _usable_evidence(item)
    ]
    context_items = [
        item
        for item in (_at(evidence, i) for i in context_indices)
        if item and _usable_evidence(item)
    ]
    # Relevance filter: low-relevance support does not inflate status.
    relevant_support = [
        item
        for item in support_items
        if relevance_for_claim(statement, item) != "LOW"
        or source_class(item) == "PRIMARY"
    ]
    groups = {hypothesis_engine.independence_group(item) for item in relevant_support}
    primary = [
        item
        for item in relevant_support
        if source_class(item) == "PRIMARY"
        or hypothesis_engine.source_quality(item) == "PRIMARY"
    ]
    conflict, conflict_note = detect_conflict(relevant_support, contra_items)
    gaps = _claim_gaps(statement, kind, evidence, relevant_support)
    best_relevance = _best_relevance(statement, relevant_support)
    best_temporal = _best_temporal(relevant_support + contra_items)
    post_only = bool(relevant_support) and all(
        graph_temporal(item, None) == "UNKNOWN"
        or (item.get("raw_payload") or {}).get("provenance", {}).get("temporal_relevance")
        == "POST_EVENT"
        for item in relevant_support
    )
    if post_only and relevant_support:
        gaps.append("Support is post-event and cannot silently prove causation.")

    status = _support_status(
        kind=kind,
        relevant=relevant_support,
        primary=primary,
        groups=groups,
        contra=contra_items,
        gaps=gaps,
        conflict=conflict,
    )
    notes = []
    if not relevant_support:
        notes.append("No relevant supporting evidence.")
    if primary:
        notes.append("Strong evidence from a primary source.")
    if conflict != "NO_CONFLICT":
        notes.append(conflict_note)
    return ClaimSupportAssessment(
        status=status,
        claim_type=kind,
        supporting_count=len(relevant_support),
        contradicting_count=len(contra_items),
        context_count=len(context_items),
        independent_support=len(groups),
        primary_support=len(primary),
        relevance=best_relevance,
        temporal_relevance=best_temporal,
        conflict=conflict,
        conflict_note=conflict_note,
        gaps=gaps,
        notes=notes,
    )


def validate_citations(
    statement: str,
    evidence: Sequence[Dict[str, Any]],
    linked_indices: Sequence[int],
    *,
    investigation_id: int = 0,
    claim_id: int = 0,
) -> Tuple[bool, str]:
    """Reject fabricated URLs and unlinked 'according to' citations."""
    if "[citation removed]" in (statement or ""):
        log_event(
            logger,
            logging.WARNING,
            "EVIDENCE_VALIDATION_FAILED",
            event="EVIDENCE_VALIDATION_FAILED",
            investigation_id=investigation_id,
            claim_id=claim_id,
            reason="fabricated_url",
        )
        return False, "Cited URL is not a persisted evidence item."
    urls = _URL_RE.findall(statement or "")
    known = {
        canonical_url(str(item.get("source_url") or ""))
        for item in evidence
        if item.get("source_url")
    }
    for url in urls:
        if canonical_url(url) not in known:
            log_event(
                logger,
                logging.WARNING,
                "EVIDENCE_VALIDATION_FAILED",
                event="EVIDENCE_VALIDATION_FAILED",
                investigation_id=investigation_id,
                claim_id=claim_id,
                reason="fabricated_url",
            )
            return False, "Cited URL is not a persisted evidence item."
    valid_links = [i for i in linked_indices if 0 <= int(i) < len(evidence)]
    if re.search(r"according to|cited in|source:", statement or "", re.I) and not valid_links:
        log_event(
            logger,
            logging.WARNING,
            "EVIDENCE_VALIDATION_FAILED",
            event="EVIDENCE_VALIDATION_FAILED",
            investigation_id=investigation_id,
            claim_id=claim_id,
            reason="missing_evidence_ref",
        )
        return False, "Citation has no persisted evidence reference."
    if any(int(i) < 0 or int(i) >= len(evidence) for i in linked_indices):
        return False, "Invalid evidence reference."
    return True, ""


def strip_unknown_citations(
    statement: str, evidence: Sequence[Dict[str, Any]]
) -> str:
    """Remove fabricated URLs so they cannot enter the persisted report."""
    known = {
        canonical_url(str(item.get("source_url") or ""))
        for item in evidence
        if item.get("source_url")
    }

    def _keep(match: re.Match) -> str:
        url = match.group(0)
        return url if canonical_url(url) in known else "[citation removed]"

    return _URL_RE.sub(_keep, statement or "")


def summarize_investigation(
    evidence: Sequence[Dict[str, Any]],
    assessments: Sequence[ClaimSupportAssessment],
) -> EvidenceSummary:
    classes = [source_class(item, derived=_is_derived(item)) for item in evidence]
    contextual = sum(
        1 for item in evidence if str(item.get("source_type") or "") in {"price", "macro"}
    )
    status_counts = {s: 0 for s in (
        "STRONGLY_SUPPORTED",
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "INSUFFICIENT_EVIDENCE",
        "CONTRADICTED",
        "UNSUPPORTED",
    )}
    for row in assessments:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
    claims_n = len(assessments)
    with_primary = sum(1 for row in assessments if row.primary_support > 0)
    gaps = sum(len(row.gaps) for row in assessments)
    conflicts = sum(1 for row in assessments if row.conflict != "NO_CONFLICT")
    return EvidenceSummary(
        total_evidence=len(evidence),
        primary=sum(1 for c in classes if c == "PRIMARY"),
        secondary=sum(1 for c in classes if c == "SECONDARY"),
        derived=sum(1 for c in classes if c == "DERIVED"),
        contextual=contextual,
        claims=claims_n,
        strongly_supported=status_counts["STRONGLY_SUPPORTED"],
        supported=status_counts["SUPPORTED"],
        partially_supported=status_counts["PARTIALLY_SUPPORTED"],
        insufficient=status_counts["INSUFFICIENT_EVIDENCE"],
        contradicted=status_counts["CONTRADICTED"],
        unsupported=status_counts["UNSUPPORTED"],
        primary_source_coverage=round(with_primary / claims_n, 4) if claims_n else 0.0,
        evidence_gaps=gaps,
        conflicts=conflicts,
    )


def build_snapshot(
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    investigation_id: int = 0,
) -> EvidenceGraphSnapshot:
    assessments: List[ClaimSupportAssessment] = []
    conflicts: List[Dict[str, Any]] = []
    gaps: List[str] = []
    for hypo in hypotheses:
        statement = str(hypo.get("statement") or "")
        linked = list(
            dict.fromkeys(
                list(hypo.get("supporting_indices") or [])
                + list(hypo.get("contradicting_indices") or [])
                + list(hypo.get("evidence_indices") or [])
            )
        )
        ok, reason = validate_citations(
            statement,
            evidence,
            linked,
            investigation_id=investigation_id,
        )
        assessment = assess_claim(
            statement,
            evidence,
            supporting_indices=hypo.get("supporting_indices") or hypo.get("evidence_indices") or [],
            contradicting_indices=hypo.get("contradicting_indices") or [],
            context_indices=hypo.get("neutral_indices") or [],
            rank=int(hypo.get("rank") or 0),
        )
        if not ok:
            assessment.status = "UNSUPPORTED"
            assessment.notes.append(reason)
            assessment.gaps.append(reason)
        assessments.append(assessment)
        if assessment.conflict != "NO_CONFLICT":
            conflicts.append(
                {
                    "statement": statement[:200],
                    "conflict": assessment.conflict,
                    "note": assessment.conflict_note,
                }
            )
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_CONFLICT_DETECTED",
                event="EVIDENCE_CONFLICT_DETECTED",
                investigation_id=investigation_id,
                relationship_type=assessment.conflict,
            )
        if assessment.status in {"INSUFFICIENT_EVIDENCE", "UNSUPPORTED"} or assessment.gaps:
            gaps.extend(assessment.gaps)
            log_event(
                logger,
                logging.INFO,
                "EVIDENCE_GAP_DETECTED",
                event="EVIDENCE_GAP_DETECTED",
                investigation_id=investigation_id,
                status=assessment.status,
            )
        log_event(
            logger,
            logging.INFO,
            f"CLAIM_{assessment.status}",
            event=f"CLAIM_{assessment.status}",
            investigation_id=investigation_id,
            status=assessment.status,
            claim_type=assessment.claim_type,
        )
    summary = summarize_investigation(evidence, assessments)
    return EvidenceGraphSnapshot(
        summary=summary,
        assessments=[a.model_dump() for a in assessments],
        conflicts=conflicts,
        gaps=list(dict.fromkeys(gaps)),
    )


def advocate_context(snapshot: EvidenceGraphSnapshot) -> Dict[str, Any]:
    return {
        "evidence_summary": snapshot.summary.model_dump(),
        "evidence_conflicts": snapshot.conflicts,
        "evidence_gaps": snapshot.gaps,
        "cherry_pick_risk": bool(snapshot.conflicts) and snapshot.summary.contradicted == 0,
        "temporal_leakage": any(
            "post-event" in gap.lower() for gap in snapshot.gaps
        ),
    }


def filter_evidence(
    items: Sequence[Dict[str, Any]],
    *,
    source_class_name: Optional[str] = None,
    temporal: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        prov = (item.get("raw_payload") or {}).get("provenance") or {}
        if source_class_name and prov.get("source_class") != source_class_name:
            if source_class(item, derived=_is_derived(item)) != source_class_name:
                continue
        if temporal and (prov.get("temporal_relevance") or graph_temporal(item, None)) != temporal:
            continue
        out.append(item)
    return out


def _support_status(
    *,
    kind: ClaimType,
    relevant: Sequence[Dict[str, Any]],
    primary: Sequence[Dict[str, Any]],
    groups: set,
    contra: Sequence[Dict[str, Any]],
    gaps: Sequence[str],
    conflict: ConflictKind,
) -> ClaimSupportStatus:
    if not relevant and not contra:
        return "INSUFFICIENT_EVIDENCE" if gaps else "UNSUPPORTED"
    if not relevant and contra:
        return "CONTRADICTED"
    if conflict == "DIRECT_CONTRADICTION" and len(contra) >= len(relevant):
        return "CONTRADICTED"
    if kind == "CAUSAL_EXPLANATION" and not primary and gaps:
        return "INSUFFICIENT_EVIDENCE"
    if (
        len(primary) >= 2
        and len(groups) >= 2
        and len(contra) <= 1
        and conflict != "DIRECT_CONTRADICTION"
    ):
        return "STRONGLY_SUPPORTED"
    if primary and len(contra) == 0:
        return "SUPPORTED"
    if relevant and contra:
        return "PARTIALLY_SUPPORTED"
    if relevant and not primary:
        return "PARTIALLY_SUPPORTED" if kind == "CAUSAL_EXPLANATION" else "SUPPORTED"
    if relevant:
        return "SUPPORTED"
    return "UNSUPPORTED"


def _claim_gaps(
    statement: str,
    kind: ClaimType,
    evidence: Sequence[Dict[str, Any]],
    support: Sequence[Dict[str, Any]],
) -> List[str]:
    gaps: List[str] = []
    types = {str(item.get("source_type") or "") for item in evidence}
    text = statement.lower()
    if any(t in text for t in _REGULATORY_TERMS):
        if not ({"filing", "ir_page"} & types) and not any(
            source_class(i) == "PRIMARY" for i in support
        ):
            gaps.append("Missing primary regulatory / company source.")
    if kind == "CAUSAL_EXPLANATION" and not any(
        source_class(i) == "PRIMARY" for i in support
    ):
        if "guid" in text or "earn" in text:
            gaps.append("Causal claim lacks a primary company or filing source.")
    return gaps


def _best_relevance(statement: str, items: Sequence[Dict[str, Any]]) -> RelevanceLevel:
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best: RelevanceLevel = "LOW"
    for item in items:
        rel = relevance_for_claim(statement, item)
        if order[rel] > order[best]:
            best = rel
    return best


def _best_temporal(items: Sequence[Dict[str, Any]]) -> GraphTemporal:
    labels = [
        (item.get("raw_payload") or {}).get("provenance", {}).get("temporal_relevance")
        or graph_temporal(item, None)
        for item in items
    ]
    for preferred in ("EVENT_TIME", "PRE_EVENT", "POST_EVENT", "UNKNOWN"):
        if preferred in labels:
            return preferred  # type: ignore[return-value]
    return "UNKNOWN"


def _usable_evidence(item: Dict[str, Any]) -> bool:
    """Failed retrievals are not evidence."""
    payload = item.get("raw_payload") or {}
    if payload.get("error") or payload.get("ok") is False:
        return False
    title = str(item.get("title") or "").lower()
    if any(token in title for token in ("failed", "not found", "disabled", "empty")):
        return False
    return True


def _is_derived(item: Dict[str, Any]) -> bool:
    prov = (item.get("raw_payload") or {}).get("provenance") or {}
    return prov.get("source_class") == "DERIVED"


def _at(items: Sequence[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
    try:
        i = int(index)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(items):
        return items[i]
    return None


def _item_text(item: Dict[str, Any]) -> str:
    return _norm(f"{item.get('title') or ''} {item.get('excerpt') or ''}")


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()

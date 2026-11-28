"""Phase 4 — deterministic hypothesis scoring, status, and ranking.

LLMs may generate or interpret; they do not sort, score, or group sources.
Support scores are relative evidence scores, not probabilities.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from app.config import MAX_HYPOTHESES
from app.models.hypothesis_schemas import (
    ConfidenceLevel,
    EvidenceClassification,
    HypothesisEngineResult,
    HypothesisRecord,
    HypothesisStatus,
    SourceQuality,
    TemporalLabel,
)
from app.observability.logger import get_logger, log_event

logger = get_logger("hypothesis_engine")

QUALITY_WEIGHT: Dict[SourceQuality, float] = {
    "PRIMARY": 1.0,
    "HIGH": 0.75,
    "MEDIUM": 0.45,
    "LOW": 0.2,
}
TEMPORAL_WEIGHT: Dict[TemporalLabel, float] = {
    "same_day": 1.0,
    "pre_event": 0.85,
    "post_event": 0.55,
    "unknown": 0.5,
    "retrospective": 0.1,
}
REPEAT_FACTOR = 0.15
MISSING_PENALTY = 0.07
PRIOR_FLOOR = 0.2

_SUPPORT_TERMS = {
    "GUIDANCE": (
        "guidance cut",
        "cut guidance",
        "lowered outlook",
        "reduced outlook",
        "margin guidance",
        "miss",
        "disappoint",
        "weak commentary",
    ),
    "EARNINGS": (
        "earnings miss",
        "missed estimates",
        "eps miss",
        "revenue miss",
        "below consensus",
    ),
    "COMPANY_SPECIFIC": (
        "company",
        "guidance",
        "earnings",
        "idiosyncratic",
        "underperformed",
    ),
    "MACRO": (
        "rates",
        "yields",
        "fed",
        "inflation",
        "macro",
        "treasury",
    ),
    "MARKET_WIDE": (
        "spy",
        "nasdaq",
        "broad market",
        "sector selloff",
        "risk-off",
        "market selloff",
        "index",
    ),
    "REGULATORY": (
        "export",
        "restriction",
        "ban",
        "tariff",
        "regulatory",
        "commerce department",
        "bis",
        "sec",
    ),
    "COMPETITOR": (
        "competitor",
        "rival",
        "amd",
        "intel",
        "broadcom",
        "product launch",
        "announced a new",
    ),
    "INDUSTRY": (
        "ai demand",
        "data center",
        "capex",
        "industry",
        "semiconductor demand",
    ),
    "TECHNICAL": (
        "profit-taking",
        "profit taking",
        "oversold",
        "technical",
        "extended",
        "positioning",
    ),
    "SENTIMENT": (
        "sentiment",
        "fear",
        "analyst downgrade",
        "downgrade",
    ),
}

_CONTRADICT_TERMS = {
    "GUIDANCE": (
        "raised guidance",
        "guidance unchanged",
        "raised outlook",
        "strong outlook",
        "reaffirmed",
    ),
    "EARNINGS": (
        "beat estimates",
        "earnings beat",
        "revenue beat",
        "topped estimates",
    ),
    "COMPANY_SPECIFIC": (
        "moved with the market",
        "in line with the sector",
        "tracks the benchmark",
    ),
    "MACRO": (
        "idiosyncratic",
        "underperformed",
        "company-specific",
    ),
    "MARKET_WIDE": (
        "underperformed",
        "residual",
        "idiosyncratic",
        "isolated",
        "outpaced the sector",
    ),
    "REGULATORY": (
        "no restriction",
        "no ban",
        "license approved",
        "no regulatory",
    ),
    "COMPETITOR": (
        "no competitor",
        "no rival announcement",
    ),
    "INDUSTRY": (
        "demand remains strong",
        "raised capex",
        "strong ai demand",
    ),
    "TECHNICAL": (
        "fundamental",
        "guidance",
        "filing",
    ),
    "SENTIMENT": (
        "upgrade",
        "primary source",
    ),
}

_STANCE_FOR_CATEGORY = {
    "COMPANY_SPECIFIC": "supports_move",
    "EARNINGS": "supports_move",
    "GUIDANCE": "supports_move",
    "REGULATORY": "supports_move",
    "COMPETITOR": "supports_move",
    "INDUSTRY": "supports_move",
    "SENTIMENT": "supports_move",
    "MACRO": "market_noise",
    "MARKET_WIDE": "market_noise",
    "TECHNICAL": "market_noise",
    "OTHER": "unknown",
}

_NEW_HYPOTHESIS_TRIGGERS = (
    (
        "COMPETITOR",
        ("competitor", "rival announced", "product launch"),
        "Competitor product announcement",
        "A rival disclosure appeared in collected evidence.",
    ),
    (
        "REGULATORY",
        ("export restriction", "export ban", "commerce department"),
        "Regulatory / export restriction",
        "Official or news evidence mentioned an export or regulatory action.",
    ),
)


def source_quality(item: Dict[str, Any]) -> SourceQuality:
    source = str(item.get("source_type") or "").lower()
    method = str(item.get("retrieval_method") or "").lower()
    if source in {"filing", "ir_page", "transcript"}:
        return "PRIMARY"
    if source == "price" or method in {"mcp", "httpx"} and source in {"price"}:
        return "HIGH"
    if source == "news":
        return "MEDIUM"
    if method in {"openclaw", "httpx", "browser_mcp"} and source == "ir_page":
        return "PRIMARY"
    if source in {"macro"}:
        return "HIGH"
    return "LOW"


def independence_group(item: Dict[str, Any]) -> str:
    """Group reprints of the same statement. Primary sources stay unique."""
    quality = source_quality(item)
    url = str(item.get("source_url") or "")
    host = (urlparse(url).hostname or "").lower().replace("www.", "")
    title = _norm_text(item.get("title") or "")[:80]
    excerpt = _norm_text(item.get("excerpt") or "")[:80]
    if quality == "PRIMARY":
        return f"primary:{host or title}"
    fingerprint = title or excerpt
    if host in {"reuters.com", "apnews.com", "associatedpress.com"}:
        return f"wire:{fingerprint}"
    if host:
        return f"host:{host}:{fingerprint[:40]}"
    return f"title:{fingerprint}"


def extract_evidence_time(item: Dict[str, Any]) -> Optional[datetime]:
    payload = item.get("raw_payload") or {}
    candidates = [
        payload.get("providerPublishTime"),
        payload.get("provider_publish_time"),
        payload.get("filing_date"),
        payload.get("date"),
        payload.get("published_at"),
        payload.get("datetime"),
        item.get("published_at"),
    ]
    for raw in candidates:
        parsed = _parse_time(raw)
        if parsed is not None:
            return parsed
    return None


def temporal_label(
    item: Dict[str, Any],
    event_time: Optional[datetime],
) -> TemporalLabel:
    published = extract_evidence_time(item)
    if published is None or event_time is None:
        return "unknown"
    delta_days = (published.date() - event_time.date()).days
    if delta_days < 0:
        return "pre_event"
    if delta_days == 0:
        return "same_day"
    if delta_days <= 3:
        return "post_event"
    return "retrospective"


def classification_weight(
    quality: SourceQuality,
    temporal: TemporalLabel,
    *,
    repeat: bool,
) -> float:
    weight = QUALITY_WEIGHT[quality] * TEMPORAL_WEIGHT[temporal]
    if temporal == "retrospective":
        weight *= 0.25
    if repeat:
        weight *= REPEAT_FACTOR
    return round(weight, 4)


def classify_evidence_item(
    item: Dict[str, Any],
    record: HypothesisRecord,
    *,
    index: int,
    event_time: Optional[datetime] = None,
    move: Optional[Any] = None,
    seen_groups: Optional[set] = None,
) -> EvidenceClassification:
    quality = source_quality(item)
    temporal = temporal_label(item, event_time)
    group = independence_group(item)
    repeat = bool(seen_groups and group in seen_groups)
    text = _item_text(item)
    relation = _relation_for(text, record, item, move)
    weight = (
        0.0
        if relation in {"neutral", "insufficient"}
        else classification_weight(quality, temporal, repeat=repeat)
    )
    if relation == "insufficient" or _is_failure(item):
        relation = "insufficient"
        weight = 0.0
    return EvidenceClassification(
        evidence_index=index,
        hypothesis_id=record.hypothesis_id,
        relation=relation,
        quality=quality,
        independence_group=group,
        temporal=temporal,
        weight=weight,
        note=_quality_note(quality),
    )


def classify_all(
    records: Sequence[HypothesisRecord],
    evidence: Sequence[Dict[str, Any]],
    *,
    event_time: Optional[datetime] = None,
    move: Optional[Any] = None,
) -> List[EvidenceClassification]:
    out: List[EvidenceClassification] = []
    for record in records:
        seen: set = set()
        for idx, item in enumerate(evidence):
            row = classify_evidence_item(
                item,
                record,
                index=idx,
                event_time=event_time,
                move=move,
                seen_groups=seen,
            )
            if row.relation in {"supports", "contradicts"}:
                seen.add(row.independence_group)
            out.append(row)
    return out


def apply_classifications(
    records: Sequence[HypothesisRecord],
    classifications: Sequence[EvidenceClassification],
    evidence: Sequence[Dict[str, Any]],
) -> List[HypothesisRecord]:
    by_id = {r.hypothesis_id: r.model_copy(deep=True) for r in records}
    for record in by_id.values():
        related = [c for c in classifications if c.hypothesis_id == record.hypothesis_id]
        support = [c for c in related if c.relation == "supports"]
        contra = [c for c in related if c.relation == "contradicts"]
        neutral = [c for c in related if c.relation == "neutral"]
        record.supporting_evidence = [c.evidence_index for c in support]
        record.contradicting_evidence = [c.evidence_index for c in contra]
        record.neutral_evidence = [c.evidence_index for c in neutral]
        support_w = sum(c.weight for c in support)
        contra_w = sum(c.weight for c in contra)
        missing = _missing_requirements(record, evidence, support, contra)
        record.missing_evidence = missing
        score = PRIOR_FLOOR + record.prior_support * 0.2 + support_w - contra_w
        score -= MISSING_PENALTY * len(missing)
        record.current_support = max(0.0, min(1.0, round(score, 4)))
        record.support_score = record.current_support
        record.confidence_level = _confidence_level(record, support)
        record.status = _status_for(record, support_w, contra_w, bool(evidence))
        record.stance = _STANCE_FOR_CATEGORY.get(record.category, "unknown")
        if record.status == "DISCARDED" and not record.discard_reason:
            record.discard_reason = (
                "No supporting evidence and required sources remain missing."
            )
    return list(by_id.values())


def rank_records(records: Sequence[HypothesisRecord]) -> List[HypothesisRecord]:
    active = [r for r in records if r.status != "DISCARDED"]
    discarded = [r for r in records if r.status == "DISCARDED"]
    active.sort(
        key=lambda r: (-r.support_score, -len(r.supporting_evidence), r.hypothesis_id)
    )
    discarded.sort(key=lambda r: r.hypothesis_id)
    ranked = active + discarded
    for i, record in enumerate(ranked, start=1):
        record.rank = i
    return ranked


def maybe_add_hypotheses(
    records: Sequence[HypothesisRecord],
    evidence: Sequence[Dict[str, Any]],
    *,
    max_hypotheses: Optional[int] = None,
) -> Tuple[List[HypothesisRecord], List[str]]:
    limit = max(3, min(8, max_hypotheses or MAX_HYPOTHESES))
    current = [r.model_copy(deep=True) for r in records]
    created: List[str] = []
    blob = " ".join(_item_text(item) for item in evidence).lower()
    existing_cats = {r.category for r in current}
    existing_titles = {_norm_text(r.title) for r in current}
    next_id = _next_hypothesis_id(current)
    for category, triggers, title, why in _NEW_HYPOTHESIS_TRIGGERS:
        if len(current) >= limit:
            break
        if category in existing_cats or _norm_text(title) in existing_titles:
            continue
        if not any(token in blob for token in triggers):
            continue
        hid = f"H{next_id}"
        next_id += 1
        current.append(
            HypothesisRecord(
                hypothesis_id=hid,
                title=title,
                description=why,
                category=category,  # type: ignore[arg-type]
                status="OPEN",
                why_plausible=why,
                required_evidence=_default_requirements(category)[0],
                contradicting_requirements=_default_requirements(category)[1],
                tool_categories=_default_tools(category),
                stance=_STANCE_FOR_CATEGORY.get(category, "unknown"),
            )
        )
        created.append(hid)
        log_event(
            logger,
            logging.INFO,
            "HYPOTHESIS_CREATED",
            event="HYPOTHESIS_CREATED",
            hypothesis_id=hid,
            category=category,
        )
    return current, created


def score_and_rank(
    records: Sequence[HypothesisRecord],
    evidence: Sequence[Dict[str, Any]],
    *,
    event_time: Optional[datetime] = None,
    move: Optional[Any] = None,
    investigation_id: int = 0,
    ticker: str = "",
    iteration: int = 0,
    allow_new: bool = True,
    max_hypotheses: Optional[int] = None,
) -> HypothesisEngineResult:
    working = [r.model_copy(deep=True) for r in records]
    created: List[str] = []
    if allow_new:
        working, created = maybe_add_hypotheses(
            working, evidence, max_hypotheses=max_hypotheses
        )
    classifications = classify_all(
        working, evidence, event_time=event_time, move=move
    )
    updated = apply_classifications(working, classifications, evidence)
    ranked = rank_records(updated)
    discarded = [r.hypothesis_id for r in ranked if r.status == "DISCARDED"]
    for record in ranked:
        log_event(
            logger,
            logging.INFO,
            "HYPOTHESIS_UPDATED",
            event="HYPOTHESIS_UPDATED",
            investigation_id=investigation_id,
            hypothesis_id=record.hypothesis_id,
            ticker=ticker,
            iteration=iteration,
            status=record.status,
            score=record.support_score,
        )
        if record.status == "DISCARDED":
            log_event(
                logger,
                logging.INFO,
                "HYPOTHESIS_DISCARDED",
                event="HYPOTHESIS_DISCARDED",
                investigation_id=investigation_id,
                hypothesis_id=record.hypothesis_id,
                ticker=ticker,
                reason=record.discard_reason,
            )
    leading = next((r for r in ranked if r.status != "DISCARDED"), None)
    alternative = next(
        (r for r in ranked if leading and r.hypothesis_id != leading.hypothesis_id and r.status != "DISCARDED"),
        None,
    )
    weakest = next(
        (r for r in reversed(ranked) if r.status != "DISCARDED"),
        ranked[-1] if ranked else None,
    )
    result = HypothesisEngineResult(
        hypotheses=ranked,
        classifications=list(classifications),
        leading_hypothesis_id=leading.hypothesis_id if leading else "",
        strongest_alternative_id=alternative.hypothesis_id if alternative else "",
        weakest_hypothesis_id=weakest.hypothesis_id if weakest else "",
        created_hypothesis_ids=created,
        discarded_hypothesis_ids=discarded,
        summary=_engine_summary(ranked, leading, alternative),
    )
    log_event(
        logger,
        logging.INFO,
        "HYPOTHESIS_RANKED",
        event="HYPOTHESIS_RANKED",
        investigation_id=investigation_id,
        ticker=ticker,
        iteration=iteration,
        leading=result.leading_hypothesis_id,
        alternative=result.strongest_alternative_id,
    )
    return result


def records_to_legacy(
    result: HypothesisEngineResult,
    evidence: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map engine records onto the existing claim/ranker dict shape."""
    active = [r for r in result.hypotheses if r.status != "DISCARDED"] or list(
        result.hypotheses
    )
    total = sum(r.support_score for r in active) or 1.0
    rows: List[Dict[str, Any]] = []
    for record in result.hypotheses:
        cited = list(
            dict.fromkeys(record.supporting_evidence + record.contradicting_evidence)
        )
        if not cited and evidence:
            cited = [0]
        weight = (
            round(record.support_score / total, 4)
            if record.status != "DISCARDED"
            else 0.0
        )
        rows.append(
            {
                "statement": record.title,
                "stance": record.stance,
                "confidence_score": record.support_score,
                "weight": weight,
                "support_score": record.support_score,
                "confidence_level": record.confidence_level,
                "hypothesis_status": record.status,
                "hypothesis_id": record.hypothesis_id,
                "category": record.category,
                "devil_advocate_notes": _counter_note(record, evidence),
                "evidence_indices": cited,
                "supporting_indices": list(record.supporting_evidence),
                "contradicting_indices": list(record.contradicting_evidence),
                "missing_evidence": list(record.missing_evidence),
                "rank": record.rank,
            }
        )
    return {
        "summary": result.summary,
        "hypotheses": rows,
        "engine": result.model_dump(),
        "leading_hypothesis_id": result.leading_hypothesis_id,
        "strongest_alternative_id": result.strongest_alternative_id,
    }


def overlay_engine_fields(
    hypotheses: Sequence[Dict[str, Any]],
    result: HypothesisEngineResult,
) -> List[Dict[str, Any]]:
    """Add engine fields to existing ranker rows without dropping mocked weights."""
    out: List[Dict[str, Any]] = []
    used = set()
    for row in hypotheses:
        clone = dict(row)
        match = _match_record(str(clone.get("statement") or ""), result.hypotheses, used)
        if match is not None:
            used.add(match.hypothesis_id)
            clone.setdefault("hypothesis_id", match.hypothesis_id)
            clone["support_score"] = match.support_score
            clone["confidence_level"] = match.confidence_level
            clone["hypothesis_status"] = match.status
            clone["supporting_indices"] = list(match.supporting_evidence)
            clone["contradicting_indices"] = list(match.contradicting_evidence)
            clone["missing_evidence"] = list(match.missing_evidence)
            clone["category"] = match.category
            if not clone.get("evidence_indices") and (
                match.supporting_evidence or match.contradicting_evidence
            ):
                clone["evidence_indices"] = list(
                    dict.fromkeys(
                        match.supporting_evidence + match.contradicting_evidence
                    )
                )
        out.append(clone)
    return out


def context_for_leading(
    result: HypothesisEngineResult,
    evidence: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    by_id = {r.hypothesis_id: r for r in result.hypotheses}
    lead = by_id.get(result.leading_hypothesis_id)
    alt = by_id.get(result.strongest_alternative_id)
    if lead is None:
        return {}
    def _rows(indices: Iterable[int]) -> List[Dict[str, Any]]:
        out = []
        for idx in indices:
            if 0 <= idx < len(evidence):
                item = evidence[idx]
                out.append(
                    {
                        "index": idx,
                        "title": item.get("title"),
                        "source_type": item.get("source_type"),
                        "quality": source_quality(item),
                    }
                )
        return out
    return {
        "leading_hypothesis_id": lead.hypothesis_id,
        "strongest_alternative_id": alt.hypothesis_id if alt else "",
        "supporting_evidence": _rows(lead.supporting_evidence),
        "contradicting_evidence": _rows(lead.contradicting_evidence),
        "missing_evidence": list(lead.missing_evidence),
        "source_quality": [
            source_quality(evidence[i])
            for i in lead.supporting_evidence
            if 0 <= i < len(evidence)
        ],
        "confidence_level": lead.confidence_level,
        "support_score": lead.support_score,
        "alternative_title": alt.title if alt else "",
    }


def _relation_for(
    text: str,
    record: HypothesisRecord,
    item: Dict[str, Any],
    move: Optional[Any],
) -> str:
    if _is_failure(item):
        return "insufficient"
    source = str(item.get("source_type") or "")
    category = record.category
    residual = getattr(move, "residual_pct", None) if move is not None else None
    try:
        residual_f = float(residual) if residual is not None else None
    except (TypeError, ValueError):
        residual_f = None

    if source == "price" and residual_f is not None:
        if category in {"MARKET_WIDE", "MACRO"}:
            return "contradicts" if abs(residual_f) >= 1.0 else "supports"
        if category in {"COMPANY_SPECIFIC", "GUIDANCE", "EARNINGS", "REGULATORY", "COMPETITOR"}:
            return "supports" if abs(residual_f) >= 1.0 else "contradicts"

    support_hits = sum(1 for term in _SUPPORT_TERMS.get(category, ()) if term in text)
    contra_hits = sum(1 for term in _CONTRADICT_TERMS.get(category, ()) if term in text)
    if support_hits and contra_hits:
        return "supports" if support_hits > contra_hits else "contradicts"
    if support_hits:
        return "supports"
    if contra_hits:
        return "contradicts"
    if source in {"news", "filing", "ir_page"} and category in {
        "COMPANY_SPECIFIC",
        "GUIDANCE",
        "EARNINGS",
    }:
        return "neutral"
    return "neutral"


def _missing_requirements(
    record: HypothesisRecord,
    evidence: Sequence[Dict[str, Any]],
    support: Sequence[EvidenceClassification],
    contra: Sequence[EvidenceClassification],
) -> List[str]:
    present_types = {str(item.get("source_type") or "") for item in evidence}
    missing: List[str] = []
    needed = list(record.required_evidence)
    mapping = {
        "filings": "filing",
        "filing": "filing",
        "sec": "filing",
        "ir": "ir_page",
        "investor": "ir_page",
        "news": "news",
        "price": "price",
        "guidance": "ir_page",
        "transcript": "transcript",
        "spy": "price",
        "sector": "price",
    }
    for req in needed:
        key = req.lower()
        matched = False
        for token, source in mapping.items():
            if token in key and source in present_types:
                matched = True
                break
        if not matched and not support and not contra:
            missing.append(req)
        elif not matched:
            # Requirement still unanswered if no classified support/contra for that family.
            if not any(token in key for token in ("spy", "sector", "price")) or "price" not in present_types:
                if not any(token in key for token in mapping):
                    continue
                if not any(token in key and mapping[token] in present_types for token in mapping):
                    missing.append(req)
    return missing[:6]


def _status_for(
    record: HypothesisRecord,
    support_w: float,
    contra_w: float,
    has_evidence: bool,
) -> HypothesisStatus:
    if not has_evidence or (
        not record.supporting_evidence
        and not record.contradicting_evidence
        and not record.neutral_evidence
    ):
        return "INSUFFICIENT_EVIDENCE" if has_evidence else "OPEN"
    if (
        support_w <= 0.05
        and contra_w <= 0.05
        and record.missing_evidence
        and not record.supporting_evidence
    ):
        return "INSUFFICIENT_EVIDENCE"
    if contra_w > support_w + 0.15 and record.contradicting_evidence:
        if support_w <= 0.08 and record.missing_evidence:
            return "DISCARDED"
        return "CONTRADICTED"
    if record.supporting_evidence and record.contradicting_evidence:
        return "WEAKENED" if record.support_score < 0.55 else "SUPPORTED"
    if record.support_score >= 0.55 and support_w > contra_w:
        return "SUPPORTED"
    if record.contradicting_evidence and support_w <= 0.08:
        return "CONTRADICTED" if contra_w >= 0.2 else "WEAKENED"
    if record.support_score < 0.18 and not record.supporting_evidence:
        return "DISCARDED"
    return "INVESTIGATING"


def _confidence_level(
    record: HypothesisRecord,
    support: Sequence[EvidenceClassification],
) -> ConfidenceLevel:
    qualities = {c.quality for c in support}
    if record.support_score >= 0.65 and qualities & {"PRIMARY", "HIGH"}:
        return "HIGH"
    if record.support_score >= 0.45:
        return "MEDIUM"
    if record.support_score >= 0.25:
        return "LOW"
    return "VERY_LOW"


def _default_requirements(category: str) -> Tuple[List[str], List[str]]:
    catalog = {
        "GUIDANCE": (
            ["current guidance", "previous guidance", "earnings commentary", "investor presentation"],
            ["guidance unchanged", "strong forward commentary"],
        ),
        "EARNINGS": (
            ["latest earnings release", "consensus comparison", "earnings transcript"],
            ["earnings beat with raised outlook"],
        ),
        "COMPANY_SPECIFIC": (
            ["company news", "official IR materials"],
            ["move explained by the benchmark"],
        ),
        "MACRO": (
            ["SPY movement", "rates / macro news"],
            ["large residual vs the benchmark"],
        ),
        "MARKET_WIDE": (
            ["SPY movement", "sector ETF movement"],
            ["ticker underperformed the sector"],
        ),
        "REGULATORY": (
            ["SEC filings", "official government or company statements", "credible regulatory news"],
            ["no material regulatory event"],
        ),
        "COMPETITOR": (
            ["competitor announcements", "industry news"],
            ["no rival product or pricing news"],
        ),
        "INDUSTRY": (
            ["industry / demand news", "fundamentals"],
            ["company-only catalyst"],
        ),
        "TECHNICAL": (
            ["price/volume context"],
            ["primary-source fundamental catalyst"],
        ),
        "SENTIMENT": (
            ["analyst or headline sentiment"],
            ["primary filing that explains the move"],
        ),
    }
    return catalog.get(category, (["relevant news"], ["contradictory official statement"]))


def _default_tools(category: str) -> List[str]:
    mapping = {
        "GUIDANCE": ["earnings", "primary_source", "filings", "news"],
        "EARNINGS": ["earnings", "fundamentals", "filings", "news"],
        "COMPANY_SPECIFIC": ["news", "primary_source"],
        "MACRO": ["price", "macro", "news"],
        "MARKET_WIDE": ["price", "news"],
        "REGULATORY": ["filings", "news", "primary_source"],
        "COMPETITOR": ["news"],
        "INDUSTRY": ["news", "fundamentals"],
        "TECHNICAL": ["price"],
        "SENTIMENT": ["news"],
        "OTHER": ["news"],
    }
    return mapping.get(category, ["news"])


def _engine_summary(
    ranked: Sequence[HypothesisRecord],
    leading: Optional[HypothesisRecord],
    alternative: Optional[HypothesisRecord],
) -> str:
    if leading is None:
        return "No competing hypotheses could be ranked."
    alt = (
        f" Strongest alternative: {alternative.title} ({alternative.confidence_level} support)."
        if alternative
        else ""
    )
    return (
        f"PRIMARY EXPLANATION: {leading.title}. "
        f"Support is {leading.confidence_level} (score {leading.support_score:.2f}, not a probability)."
        f"{alt}"
    )


def _counter_note(
    record: HypothesisRecord, evidence: Sequence[Dict[str, Any]]
) -> str:
    bits = []
    if record.contradicting_evidence:
        titles = [
            str(evidence[i].get("title") or "")
            for i in record.contradicting_evidence
            if 0 <= i < len(evidence)
        ]
        bits.append("Contradicts: " + "; ".join(t for t in titles if t)[:240])
    if record.missing_evidence:
        bits.append("Missing: " + ", ".join(record.missing_evidence[:3]))
    return " ".join(bits)[:4000]


def _quality_note(quality: SourceQuality) -> str:
    return {
        "PRIMARY": "Strong evidence from a primary source.",
        "HIGH": "High-quality financial data provider.",
        "MEDIUM": "Reputable financial news; treat as secondary.",
        "LOW": "Low-quality or unverified source.",
    }[quality]


def _item_text(item: Dict[str, Any]) -> str:
    return _norm_text(
        f"{item.get('title') or ''} {item.get('excerpt') or ''}"
    )


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower()).strip()


def _is_failure(item: Dict[str, Any]) -> bool:
    title = (item.get("title") or "").lower()
    payload = item.get("raw_payload") or {}
    return bool(payload.get("error")) or "unavailable" in title or "failed" in title


def _parse_time(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text[:19].replace("Z", ""), fmt.replace("Z", ""))
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _next_hypothesis_id(records: Sequence[HypothesisRecord]) -> int:
    nums = []
    for record in records:
        match = re.match(r"H(\d+)$", record.hypothesis_id or "")
        if match:
            nums.append(int(match.group(1)))
    return (max(nums) + 1) if nums else 1


def _match_record(
    statement: str,
    records: Sequence[HypothesisRecord],
    used: set,
) -> Optional[HypothesisRecord]:
    needle = _norm_text(statement)
    for record in records:
        if record.hypothesis_id in used:
            continue
        title = _norm_text(record.title)
        if needle == title or needle in title or title in needle:
            return record
    for record in records:
        if record.hypothesis_id not in used:
            return record
    return None

"""Phase 7 — deterministic four-layer verification on the existing ledger.

Does not treat LLM text as evidence. Reuses Phase 5 evidence_graph.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from app.models.verification_schemas import (
    ChallengeRecommendation,
    ChallengeSeverity,
    ClaimEvidenceCheck,
    ConclusionCheck,
    ConclusionStrength,
    ConfidenceLabel,
    DevilsAdvocateResult,
    EvidenceGapReport,
    FinalInvestigationReport,
    HypothesisCheck,
    SourceCheck,
    VerificationReport,
    VerificationSummary,
)
from app.observability.logger import get_logger, log_event
from app.services import evidence_graph, hypothesis_engine

logger = get_logger("verification_engine")

_INSUFFICIENT_CAUSE = "Insufficient evidence to determine the primary cause."
_CAUSAL_INCOMPLETE = (
    "{core} appear to be the strongest supported explanation, but the "
    "evidence does not establish causality with certainty."
)
_CAUSAL_STRONG = (
    "{core} were a significant contributor to the decline, based on the "
    "company's release and contemporaneous market reaction."
)
_SAID_RE = re.compile(
    r"(management|the company|executives?|c[fe]o)\s+said\s+(.{8,200})",
    re.I,
)
_QUOTED_RE = re.compile(r'"([^"]{8,200})"')
_CAUSAL_LANGUAGE = re.compile(
    r"\b(caused|because|drove|contributed|attribution|attributed)\b",
    re.I,
)
_OFFICIAL_TYPES = {"filing", "ir_page", "transcript"}
_URL_OPTIONAL = {"price", "macro", "options"}
_WEAKENER_HINTS = (
    "guidance unchanged",
    "annual guidance unchanged",
    "raised outlook",
    "revenue exceeded",
    "beat estimates",
    "revenue beat",
    "sector decline",
    "semiconductor weakness",
    "market-wide",
    "broad selloff",
)


def verify_investigation(
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    investigation_id: int = 0,
    event_time: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], VerificationReport]:
    """Run levels 1–4. Returns possibly rewritten hypotheses plus a report."""
    log_event(
        logger,
        logging.INFO,
        "VERIFICATION_STARTED",
        event="VERIFICATION_STARTED",
        investigation_id=investigation_id,
    )
    items = list(evidence or [])
    source_checks = [check_source(i, item, event_time=event_time) for i, item in enumerate(items)]
    claim_checks: List[ClaimEvidenceCheck] = []
    rewritten: List[Dict[str, Any]] = []
    hedged: List[str] = []
    notes: List[str] = []

    snapshot = evidence_graph.build_snapshot(
        hypotheses, items, investigation_id=investigation_id
    )

    for idx, hypo in enumerate(hypotheses):
        clone = dict(hypo)
        statement = str(clone.get("statement") or "")
        assessment = evidence_graph.assess_claim(
            statement,
            items,
            supporting_indices=clone.get("supporting_indices")
            or clone.get("evidence_indices")
            or [],
            contradicting_indices=clone.get("contradicting_indices") or [],
            context_indices=clone.get("neutral_indices") or [],
            rank=int(clone.get("rank") or 0),
        )
        check = check_claim_evidence(clone, items, assessment, event_time=event_time)
        check.contradictions = list(
            dict.fromkeys(check.contradictions + find_weakeners(statement, items))
        )
        claim_checks.append(check)
        if check.retrospective:
            notes.append(
                "Retrospective (post-event) evidence must not silently prove causation."
            )
        if check.contradictions:
            notes.extend(check.contradictions[:3])
            log_event(
                logger,
                logging.INFO,
                "CONTRADICTION_DETECTED",
                event="CONTRADICTION_DETECTED",
                investigation_id=investigation_id,
                hypothesis_id=str(clone.get("hypothesis_id") or ""),
                verification_status=assessment.status,
            )
        new_statement, did_hedge = apply_causal_guardrail(
            statement,
            assessment.claim_type,
            assessment.status,
            alternative=_alternative_title(hypotheses, idx),
        )
        if did_hedge:
            hedged.append(new_statement)
            clone["statement"] = new_statement
            log_event(
                logger,
                logging.INFO,
                "CAUSAL_CLAIM_HEDGED",
                event="CAUSAL_CLAIM_HEDGED",
                investigation_id=investigation_id,
            )
            log_event(
                logger,
                logging.INFO,
                "CAUSALITY_CHALLENGED",
                event="CAUSALITY_CHALLENGED",
                investigation_id=investigation_id,
                hypothesis_id=str(clone.get("hypothesis_id") or ""),
                verification_status=assessment.status,
            )
        clone["support_status"] = assessment.status
        clone["claim_type"] = assessment.claim_type
        rewritten.append(clone)

    rewritten, rejected_hallucinations = finalize_claims(
        rewritten, items, investigation_id=investigation_id
    )
    notes.extend(f"Rejected unsupported claim: {s}" for s in rejected_hallucinations[:4])
    hyp_check = check_hypothesis_layer(rewritten, items, snapshot)
    conclusion = check_conclusion_layer(rewritten, claim_checks, hedged)
    gaps = build_evidence_gap_report(items, hyp_check.gaps)
    strength = assess_conclusion_strength(
        rewritten[0] if rewritten else {},
        claim_checks[0] if claim_checks else None,
        has_primary=_has_primary(items, rewritten[0]) if rewritten else False,
    )
    lead_kind = str((rewritten[0].get("claim_type") if rewritten else "") or "")
    lead_stmt = str((rewritten[0].get("statement") if rewritten else "") or "")
    if (
        strength == "INSUFFICIENT_EVIDENCE"
        and rewritten
        and lead_kind in {"CAUSAL_EXPLANATION", "CONCLUSION", ""}
        and _CAUSAL_LANGUAGE.search(lead_stmt)
    ):
        rewritten[0]["statement"] = _INSUFFICIENT_CAUSE
        rewritten[0]["support_status"] = "INSUFFICIENT_EVIDENCE"
        log_event(
            logger,
            logging.INFO,
            "CONCLUSION_DOWNGRADED",
            event="CONCLUSION_DOWNGRADED",
            investigation_id=investigation_id,
            verification_status=strength,
            hypothesis_id=str(rewritten[0].get("hypothesis_id") or ""),
        )
    reasons = research_more_reasons(
        rewritten[0] if rewritten else {},
        items,
        claim_checks[0] if claim_checks else None,
        alternative=rewritten[1] if len(rewritten) > 1 else {},
    )
    summary = build_verification_summary(
        claim_checks, items, strength, gaps, rejected_hallucinations
    )
    report = VerificationReport(
        source_checks=source_checks,
        claim_checks=claim_checks,
        hypothesis_check=hyp_check,
        conclusion_check=conclusion,
        notes=list(dict.fromkeys(notes + hyp_check.issues + conclusion.issues + gaps.gaps))[:16],
        passed=bool(hyp_check.leading_supported or any(h.get("evidence_indices") for h in rewritten)),
        summary=summary,
        evidence_gaps=gaps,
        conclusion_strength=strength,
        research_more_reasons=reasons,
        rejected_claims=rejected_hallucinations[:8],
    )
    log_event(
        logger,
        logging.INFO,
        "VERIFICATION_COMPLETED",
        event="VERIFICATION_COMPLETED",
        investigation_id=investigation_id,
        source_issues=sum(1 for s in source_checks if not s.ok),
        claim_issues=sum(1 for c in claim_checks if not c.ok),
        passed=report.passed,
    )
    return rewritten, report


def check_source(
    index: int,
    item: Dict[str, Any],
    *,
    event_time: Optional[datetime] = None,
) -> SourceCheck:
    issues: List[str] = []
    source_type = str(item.get("source_type") or "")
    url = str(item.get("source_url") or "")
    excerpt = str(item.get("excerpt") or "").strip()
    title = str(item.get("title") or "").strip()
    payload = item.get("raw_payload") or {}
    prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    if not title and not excerpt:
        issues.append("Evidence content is empty.")
    if payload.get("error") or payload.get("ok") is False:
        issues.append("Retrieval recorded an error.")
    if source_type not in _URL_OPTIONAL:
        if not url:
            issues.append("Source URL is missing.")
        else:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                issues.append("Source URL is not a valid http(s) URL.")
            elif source_type in _OFFICIAL_TYPES:
                host = (parsed.hostname or "").lower()
                if host and not _looks_official(host):
                    issues.append(f"Domain {host} is not an official primary-source host.")
    if source_type in {"news", "ir_page", "filing", "transcript"}:
        published = (
            prov.get("published_at")
            or payload.get("date")
            or payload.get("published_at")
            or item.get("published_at")
        )
        if not published:
            issues.append("Publication date is missing where expected.")
    retrieved = prov.get("retrieved_at") or item.get("created_at")
    if source_type not in _URL_OPTIONAL and not retrieved:
        issues.append("Retrieval timestamp is missing.")
    if not source_type:
        issues.append("Source type is missing.")
    temporal = str(prov.get("temporal_relevance") or evidence_graph.graph_temporal(item, event_time))
    group = str(
        prov.get("source_group")
        or item.get("source_group")
        or hypothesis_engine.independence_group(item)
    )
    return SourceCheck(
        evidence_index=index,
        ok=not issues,
        issues=issues,
        source_type=source_type,
        source_url=url,
        source_group=group,
        temporal_relevance=temporal,
    )


def check_claim_evidence(
    hypo: Dict[str, Any],
    evidence: Sequence[Dict[str, Any]],
    assessment: Any,
    *,
    event_time: Optional[datetime] = None,
) -> ClaimEvidenceCheck:
    issues: List[str] = []
    statement = str(hypo.get("statement") or "")
    linked = list(
        dict.fromkeys(
            list(hypo.get("supporting_indices") or [])
            + list(hypo.get("contradicting_indices") or [])
            + list(hypo.get("evidence_indices") or [])
        )
    )
    ok, reason = evidence_graph.validate_citations(statement, evidence, linked)
    if not ok:
        issues.append(reason)
    if assessment.supporting_count == 0 and assessment.contradicting_count == 0:
        issues.append("Claim has no usable supporting or contradicting evidence.")
    if assessment.status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"}:
        issues.append(f"Support status is {assessment.status}.")
    support_idxs = list(hypo.get("supporting_indices") or hypo.get("evidence_indices") or [])
    labels = [_item_temporal(evidence_graph._at(evidence, i), event_time) for i in support_idxs]
    labels = [lab for lab in labels if lab]
    retrospective = bool(labels) and all(lab == "POST_EVENT" for lab in labels)
    if assessment.temporal_relevance == "POST_EVENT" and assessment.supporting_count:
        retrospective = True
    if retrospective:
        issues.append("Support is retrospective / post-event.")
    contradictions = []
    if assessment.conflict != "NO_CONFLICT":
        contradictions.append(assessment.conflict_note or assessment.conflict)
    groups = set()
    for idx in support_idxs:
        item = evidence_graph._at(evidence, idx)
        if not item:
            continue
        payload = item.get("raw_payload") or {}
        prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
        groups.add(
            str(
                prov.get("source_group")
                or item.get("source_group")
                or hypothesis_engine.independence_group(item)
            )
        )
    return ClaimEvidenceCheck(
        statement=statement,
        claim_type=assessment.claim_type,
        ok=not issues,
        issues=issues,
        support_status=assessment.status,
        contradictions=contradictions,
        retrospective=retrospective,
        independent_support=len(groups) or assessment.independent_support,
    )


def check_hypothesis_layer(
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    snapshot: Any,
) -> HypothesisCheck:
    issues: List[str] = []
    if not hypotheses:
        return HypothesisCheck(issues=["No hypotheses to verify."])
    lead = hypotheses[0]
    lead_status = str(lead.get("support_status") or "")
    leading_supported = lead_status in {
        "SUPPORTED",
        "STRONGLY_SUPPORTED",
        "PARTIALLY_SUPPORTED",
    } and bool(lead.get("evidence_indices"))
    if not leading_supported:
        issues.append("Leading hypothesis lacks sufficient supporting evidence.")
    alt = hypotheses[1] if len(hypotheses) > 1 else None
    alternative_considered = alt is not None
    if not alternative_considered:
        issues.append("No strongest alternative was ranked.")
    contradictions = bool(snapshot.conflicts) or bool(lead.get("contradicting_indices"))
    if snapshot.conflicts:
        issues.append("Contradictory evidence is present and must remain visible.")
    gaps = list(snapshot.gaps or [])
    if gaps:
        issues.append(gaps[0])
    return HypothesisCheck(
        leading_supported=leading_supported,
        alternative_considered=alternative_considered,
        contradictions_considered=contradictions or not snapshot.conflicts,
        gaps=gaps[:8],
        issues=issues,
    )


def check_conclusion_layer(
    hypotheses: Sequence[Dict[str, Any]],
    claim_checks: Sequence[ClaimEvidenceCheck],
    hedged: Sequence[str],
) -> ConclusionCheck:
    issues: List[str] = []
    causal_rows = [c for c in claim_checks if c.claim_type == "CAUSAL_EXPLANATION"]
    causal_justified = (
        all(
            c.support_status in {"SUPPORTED", "STRONGLY_SUPPORTED"} and not c.retrospective
            for c in causal_rows
        )
        if causal_rows
        else True
    )
    if causal_rows and not causal_justified:
        issues.append("Causal language is not fully justified; statements were hedged.")
    uncertainty = bool(hedged) or any(
        c.support_status in {"PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE", "CONTRADICTED"}
        for c in claim_checks
    )
    return ConclusionCheck(
        causal_justified=causal_justified,
        uncertainty_represented=bool(hedged) or uncertainty or not causal_rows,
        hedged_statements=list(hedged),
        issues=issues,
    )


def apply_causal_guardrail(
    statement: str,
    claim_type: str,
    status: str,
    *,
    alternative: str = "",
) -> Tuple[str, bool]:
    """Hedge causal claims that are not strongly evidenced. Never invent facts."""
    if claim_type != "CAUSAL_EXPLANATION":
        return statement, False
    if not _CAUSAL_LANGUAGE.search(statement or ""):
        return statement, False
    if status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"}:
        return _INSUFFICIENT_CAUSE, True
    if re.search(r"strongest supported explanation|does not establish causality", statement or "", re.I):
        return statement, False
    if re.search(r"significant contributor to the decline", statement or "", re.I):
        return statement, False
    core = _causal_core(statement)
    if status == "STRONGLY_SUPPORTED":
        return _CAUSAL_STRONG.format(core=core)[:4000], True
    text = _CAUSAL_INCOMPLETE.format(core=core)
    if alternative:
        text = (
            f"{core} appear to be the strongest supported explanation, but the "
            f"evidence does not establish causality with certainty. "
            f"{alternative.rstrip('.')} also remains plausible."
        )
    return text[:4000], True


def find_weakeners(statement: str, evidence: Sequence[Dict[str, Any]]) -> List[str]:
    """Inspect existing evidence for facts that would make the claim weaker."""
    claim = (statement or "").lower()
    out: List[str] = []
    for item in evidence:
        body = f"{item.get('title') or ''} {item.get('excerpt') or ''}".lower()
        if not body.strip():
            continue
        for hint in _WEAKENER_HINTS:
            if hint in body and (
                "guid" in claim
                or "margin" in claim
                or "caused" in claim
                or "contributed" in claim
                or "decline" in claim
            ):
                title = str(item.get("title") or hint)
                out.append(f"Contradicts or weakens the claim: {title}")
                break
    return list(dict.fromkeys(out))[:8]


def tools_for_additional_evidence(needs: Sequence[str]) -> List[str]:
    """Map Devil's Advocate evidence requests onto existing MCP tools."""
    tools: List[str] = []
    blob = " ".join(str(n).lower() for n in needs)
    if any(tok in blob for tok in ("filing", "8-k", "8k", "sec", "edgar")):
        tools.append("get_recent_filings")
    if any(
        tok in blob
        for tok in ("presentation", "transcript", "ir", "shareholder", "primary")
    ):
        tools.append("get_ir_materials")
        if "presentation" in blob or "pdf" in blob:
            tools.append("browser_find_document")
    if any(tok in blob for tok in ("news", "headline")):
        tools.append("get_stock_news")
    if any(tok in blob for tok in ("spy", "sector", "market", "price", "beta")):
        tools.append("get_price_history")
    if "primary" in blob or "causal claim" in blob or "insufficient" in blob:
        if "get_ir_materials" not in tools:
            tools.append("get_ir_materials")
        if "get_recent_filings" not in tools:
            tools.append("get_recent_filings")
    if "temporal" in blob and "get_stock_news" not in tools:
        tools.append("get_stock_news")
    seen = set()
    ordered = []
    for tool in tools:
        if tool not in seen:
            seen.add(tool)
            ordered.append(tool)
    return ordered


def unused_challenge_tools(
    needs: Sequence[str],
    *,
    existing_tools: Sequence[str],
    evidence: Sequence[Dict[str, Any]],
) -> List[str]:
    """Only request tools that are unused and not already satisfied by evidence."""
    have = {str(item.get("source_type") or "") for item in evidence}
    news_n = sum(1 for item in evidence if item.get("source_type") == "news")
    used = set(existing_tools)
    out: List[str] = []
    for tool in tools_for_additional_evidence(needs):
        if tool in used:
            continue
        if tool == "get_recent_filings" and "filing" in have:
            continue
        if tool == "get_ir_materials" and ("ir_page" in have or "transcript" in have):
            continue
        if tool == "browser_find_document" and "ir_page" in have:
            continue
        if tool == "get_stock_news" and news_n >= 2:
            continue
        if tool == "get_price_history" and "price" in have:
            continue
        out.append(tool)
    return out


def build_devils_advocate_result(
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    da_raw: Optional[Dict[str, Any]] = None,
    verification_report: Optional[VerificationReport] = None,
    hypothesis_context: Optional[Dict[str, Any]] = None,
) -> DevilsAdvocateResult:
    """Structured Devil's Advocate result. Deterministic severity; LLM fills prose."""
    raw = da_raw or {}
    ctx = hypothesis_context or {}
    report = verification_report
    lead = hypotheses[0] if hypotheses else {}
    alt = hypotheses[1] if len(hypotheses) > 1 else {}
    lead_id = str(
        lead.get("hypothesis_id")
        or ctx.get("leading_hypothesis_id")
        or ""
    )
    alt_id = str(
        alt.get("hypothesis_id")
        or ctx.get("strongest_alternative_id")
        or ""
    )
    status = str(lead.get("support_status") or "")
    lead_check = (report.claim_checks[0] if report and report.claim_checks else None)
    if not status and lead_check:
        status = lead_check.support_status
    contradictions = list(lead.get("contradicting_indices") or [])
    contra_titles = []
    for idx in contradictions:
        item = evidence_graph._at(evidence, idx)
        if item:
            contra_titles.append(str(item.get("title") or f"evidence[{idx}]"))
    if lead_check:
        contra_titles.extend(lead_check.contradictions)
    for row in ctx.get("contradicting_evidence") or []:
        if isinstance(row, dict) and row.get("title"):
            contra_titles.append(str(row["title"]))
    ignored = _as_list(raw.get("ignored_evidence")) or [
        str(t) for t in contra_titles[:4]
    ]
    assumptions = []
    if (lead.get("claim_type") or (lead_check.claim_type if lead_check else "")) == "CAUSAL_EXPLANATION":
        if status not in {"STRONGLY_SUPPORTED"}:
            assumptions.append(
                "Treating a reported fact (for example weaker guidance) as proof it caused the print."
            )
    if ctx.get("temporal_leakage") or (lead_check and lead_check.retrospective):
        assumptions.append("Post-event commentary is being read as the original cause.")
    if ctx.get("cherry_pick_risk"):
        assumptions.append("Supporting items may be over-weighted relative to contradictions.")
    extra_needed = _as_list(raw.get("additional_evidence_needed"))
    if not extra_needed:
        extra_needed = list(ctx.get("missing_evidence") or [])[:4]
        if report and report.hypothesis_check.gaps:
            extra_needed.extend(report.hypothesis_check.gaps[:2])
    extra_needed = list(dict.fromkeys(str(x) for x in extra_needed if x))[:8]

    severity, recommendation = classify_challenge(
        status=status,
        outcome=str(raw.get("outcome") or "held"),
        alternative_more_plausible=bool(raw.get("alternative_more_plausible")),
        contradictions=bool(contra_titles or ctx.get("evidence_conflicts")),
        cherry_pick=bool(ctx.get("cherry_pick_risk")),
        temporal_leakage=bool(ctx.get("temporal_leakage") or (lead_check and lead_check.retrospective)),
        leading_supported=bool(report.hypothesis_check.leading_supported) if report else bool(lead.get("evidence_indices")),
        has_primary=_has_primary(evidence, lead),
        claim_type=str(lead.get("claim_type") or (lead_check.claim_type if lead_check else "")),
        additional_needed=extra_needed,
    )
    counter = str(
        raw.get("strongest_counterargument")
        or raw.get("counterargument")
        or "Alternative explanations remain plausible; treat the lead as provisional."
    )[:4000]
    alt_strength = str(
        raw.get("alternative_strength")
        or alt.get("confidence_level")
        or ("stronger" if raw.get("alternative_more_plausible") else "plausible")
    )
    return DevilsAdvocateResult(
        target_hypothesis_id=lead_id,
        strongest_counterargument=counter,
        contradictory_evidence=list(dict.fromkeys(contra_titles))[:8],
        ignored_evidence=list(dict.fromkeys(ignored))[:8],
        questionable_assumptions=list(dict.fromkeys(assumptions))[:8],
        alternative_hypothesis_id=alt_id,
        alternative_strength=alt_strength[:80],
        additional_evidence_needed=extra_needed,
        challenge_severity=severity,
        recommendation=recommendation,
        overweighted_evidence=str(raw.get("overweighted_evidence") or "")[:500],
        notes=list(raw.get("notes") or [])[:8],
    )


def classify_challenge(
    *,
    status: str,
    outcome: str,
    alternative_more_plausible: bool,
    contradictions: bool,
    cherry_pick: bool,
    temporal_leakage: bool,
    leading_supported: bool,
    has_primary: bool,
    claim_type: str,
    additional_needed: Sequence[str],
) -> Tuple[ChallengeSeverity, ChallengeRecommendation]:
    if status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"} or not leading_supported:
        return "CRITICAL", "RESEARCH_MORE" if additional_needed else "REJECT"
    if outcome == "demoted" or alternative_more_plausible:
        return "HIGH", "RESEARCH_MORE" if additional_needed else "REVIEW"
    if cherry_pick and contradictions:
        return "HIGH", "RESEARCH_MORE" if additional_needed else "REVIEW"
    if claim_type == "CAUSAL_EXPLANATION" and not has_primary:
        return "HIGH", "RESEARCH_MORE" if additional_needed else "REVIEW"
    if outcome == "confidence_cut" or contradictions or temporal_leakage:
        return "MEDIUM", "REVIEW"
    return "LOW", "ACCEPT"


def should_request_challenge_research(
    result: DevilsAdvocateResult,
    *,
    unused_tools: Sequence[str],
    challenge_loops: int,
    max_loops: int,
    iteration_count: int = 0,
    max_iterations: int = 0,
    research_reasons: Optional[Sequence[str]] = None,
    apply_reasons: bool = False,
) -> bool:
    if challenge_loops >= max_loops:
        return False
    if max_iterations and iteration_count >= max_iterations:
        return False
    if not unused_tools:
        return False
    if result.recommendation in {"RESEARCH_MORE", "REJECT"}:
        return True
    if result.challenge_severity in {"HIGH", "CRITICAL"}:
        return True
    return bool(apply_reasons and research_reasons)


def research_more_reasons(
    lead: Dict[str, Any],
    evidence: Sequence[Dict[str, Any]],
    claim_check: Optional[ClaimEvidenceCheck],
    *,
    alternative: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Deterministic triggers for one more research pass. Not an infinite loop."""
    reasons: List[str] = []
    status = str((claim_check.support_status if claim_check else "") or lead.get("support_status") or "")
    claim_type = str((claim_check.claim_type if claim_check else "") or lead.get("claim_type") or "")
    if not _has_primary(evidence, lead):
        reasons.append("leading hypothesis lacks primary evidence")
    if status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"}:
        reasons.append("evidence is insufficient")
    if claim_type == "CAUSAL_EXPLANATION" and status not in {"SUPPORTED", "STRONGLY_SUPPORTED"}:
        reasons.append("causal claim is unsupported")
    conflict = ""
    if claim_check and claim_check.contradictions:
        conflict = " ".join(claim_check.contradictions).lower()
    if "direct" in conflict or "disagree" in conflict:
        reasons.append("contradiction is strong")
    alt = alternative or {}
    try:
        lead_w = float(lead.get("weight") or lead.get("support_score") or 0)
        alt_w = float(alt.get("weight") or alt.get("support_score") or 0)
    except (TypeError, ValueError):
        lead_w = alt_w = 0.0
    if alt and lead_w and alt_w and abs(lead_w - alt_w) <= 0.12:
        reasons.append("strongest alternative is close to leading hypothesis")
    qualities = []
    for idx in lead.get("supporting_indices") or lead.get("evidence_indices") or []:
        item = evidence_graph._at(evidence, idx)
        if item:
            qualities.append(hypothesis_engine.source_quality(item))
    if qualities and all(q in {"LOW", "MEDIUM"} for q in qualities) and "PRIMARY" not in qualities:
        reasons.append("source quality is weak")
    if claim_check and (claim_check.retrospective or "POST_EVENT" in (claim_check.issues or [])):
        reasons.append("temporal validity is questionable")
    return list(dict.fromkeys(reasons))


def assess_conclusion_strength(
    lead: Dict[str, Any],
    claim_check: Optional[ClaimEvidenceCheck],
    *,
    has_primary: bool,
    challenge_severity: str = "",
) -> ConclusionStrength:
    status = str((claim_check.support_status if claim_check else "") or lead.get("support_status") or "")
    if status == "CONTRADICTED":
        return "CONTRADICTED"
    if status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"} or not lead.get("evidence_indices"):
        return "INSUFFICIENT_EVIDENCE"
    if challenge_severity == "CRITICAL":
        return "INSUFFICIENT_EVIDENCE"
    if status == "PARTIALLY_SUPPORTED" or (claim_check and claim_check.retrospective):
        return "PARTIALLY_SUPPORTED"
    if challenge_severity == "HIGH":
        return "UNCERTAIN"
    if status == "STRONGLY_SUPPORTED" and has_primary and challenge_severity in {"", "LOW"}:
        return "ROBUST"
    if status == "SUPPORTED":
        return "SUPPORTED"
    return "UNCERTAIN"


def confidence_label(strength: str) -> ConfidenceLabel:
    if strength == "ROBUST":
        return "HIGH"
    if strength in {"SUPPORTED", "PARTIALLY_SUPPORTED"}:
        return "MEDIUM"
    return "LOW"


def display_conclusion(strength: str) -> str:
    return {
        "ROBUST": "SUPPORTED",
        "SUPPORTED": "SUPPORTED WITH QUALIFICATIONS",
        "PARTIALLY_SUPPORTED": "PARTIALLY SUPPORTED",
        "UNCERTAIN": "UNCERTAIN",
        "INSUFFICIENT_EVIDENCE": "INSUFFICIENT_EVIDENCE",
        "CONTRADICTED": "CONTRADICTED",
    }.get(strength, "UNCERTAIN")


def build_evidence_gap_report(
    evidence: Sequence[Dict[str, Any]],
    extra_gaps: Sequence[str] = (),
) -> EvidenceGapReport:
    types = {str(item.get("source_type") or "") for item in evidence if _usable_item(item)}
    news = sum(1 for item in evidence if item.get("source_type") == "news" and _usable_item(item))
    gaps: List[str] = []
    missing_mgmt = not ({"transcript", "ir_page"} & types)
    missing_reg = "filing" not in types
    missing_primary = not ({"filing", "ir_page", "transcript"} & types)
    only_secondary = news > 0 and missing_primary
    if missing_mgmt:
        gaps.append("No official management commentary available.")
    if missing_reg:
        gaps.append("No primary-source regulatory announcement found.")
    if only_secondary:
        gaps.append("Only secondary news sources available.")
    for extra in extra_gaps:
        if extra and extra not in gaps:
            gaps.append(str(extra))
    return EvidenceGapReport(
        gaps=gaps[:10],
        only_secondary_news=only_secondary,
        missing_primary=missing_primary,
        missing_management_commentary=missing_mgmt,
        missing_regulatory=missing_reg,
    )


def build_verification_summary(
    claim_checks: Sequence[ClaimEvidenceCheck],
    evidence: Sequence[Dict[str, Any]],
    strength: str,
    gaps: EvidenceGapReport,
    rejected: Sequence[str] = (),
    *,
    da_severity: str = "",
) -> VerificationSummary:
    supported = sum(1 for c in claim_checks if c.support_status in {"SUPPORTED", "STRONGLY_SUPPORTED"})
    partial = sum(1 for c in claim_checks if c.support_status == "PARTIALLY_SUPPORTED")
    unsupported = sum(1 for c in claim_checks if c.support_status in {"UNSUPPORTED", "INSUFFICIENT_EVIDENCE"}) + len(rejected)
    contradicted = sum(1 for c in claim_checks if c.support_status == "CONTRADICTED")
    primary_n = sum(
        1
        for item in evidence
        if evidence_graph.source_class(item) == "PRIMARY" and _usable_item(item)
    )
    coverage: ConfidenceLabel = "HIGH" if primary_n >= 2 else ("MEDIUM" if primary_n == 1 else "LOW")
    major = ""
    for check in claim_checks:
        if check.contradictions:
            major = check.contradictions[0]
            break
    return VerificationSummary(
        claims_reviewed=len(claim_checks),
        supported=supported,
        partially_supported=partial,
        unsupported=unsupported,
        contradicted=contradicted,
        primary_source_coverage=coverage,
        major_contradiction=major[:400],
        devil_advocate_severity=da_severity,
        final_conclusion=display_conclusion(strength),
        conclusion_strength=strength,  # type: ignore[arg-type]
        confidence=confidence_label(strength),
    )


def finalize_claims(
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    *,
    investigation_id: int = 0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Reject fabricated quotes, URLs, and unlinked citations. Do not invent replacements."""
    kept: List[Dict[str, Any]] = []
    rejected: List[str] = []
    corpus = " ".join(
        f"{item.get('title') or ''} {item.get('excerpt') or ''}" for item in evidence
    ).lower()
    for hypo in hypotheses:
        clone = dict(hypo)
        statement = str(clone.get("statement") or "")
        linked = list(
            dict.fromkeys(
                list(clone.get("supporting_indices") or [])
                + list(clone.get("contradicting_indices") or [])
                + list(clone.get("evidence_indices") or [])
            )
        )
        ok, reason = evidence_graph.validate_citations(statement, evidence, linked)
        if not ok:
            rejected.append(statement)
            log_event(
                logger,
                logging.WARNING,
                "CLAIM_VERIFICATION_FAILED",
                event="CLAIM_VERIFICATION_FAILED",
                investigation_id=investigation_id,
                hypothesis_id=str(clone.get("hypothesis_id") or ""),
                verification_status="UNSUPPORTED",
            )
            log_event(
                logger,
                logging.WARNING,
                "UNSUPPORTED_CLAIM_REJECTED",
                event="UNSUPPORTED_CLAIM_REJECTED",
                investigation_id=investigation_id,
                hypothesis_id=str(clone.get("hypothesis_id") or ""),
                verification_status="UNSUPPORTED",
            )
            continue
        if _fabricated_attribution(statement, corpus):
            rejected.append(statement)
            log_event(
                logger,
                logging.WARNING,
                "UNSUPPORTED_CLAIM_REJECTED",
                event="UNSUPPORTED_CLAIM_REJECTED",
                investigation_id=investigation_id,
                hypothesis_id=str(clone.get("hypothesis_id") or ""),
                verification_status="UNSUPPORTED",
            )
            continue
        if linked and any(int(i) < 0 or int(i) >= len(evidence) for i in linked if str(i).lstrip("-").isdigit()):
            rejected.append(statement)
            continue
        clone["support_status"] = clone.get("support_status") or "SUPPORTED"
        kept.append(clone)
        log_event(
            logger,
            logging.INFO,
            "CLAIM_VERIFIED",
            event="CLAIM_VERIFIED",
            investigation_id=investigation_id,
            hypothesis_id=str(clone.get("hypothesis_id") or ""),
            verification_status=str(clone.get("support_status") or ""),
        )
    if not kept and evidence:
        kept.append(
            {
                "statement": _INSUFFICIENT_CAUSE,
                "stance": "unknown",
                "confidence_score": 0.35,
                "weight": 1.0,
                "evidence_indices": [0],
                "support_status": "INSUFFICIENT_EVIDENCE",
                "claim_type": "CONCLUSION",
                "rank": 1,
            }
        )
    return kept, rejected


def build_final_report(
    *,
    move_line: str,
    hypotheses: Sequence[Dict[str, Any]],
    evidence: Sequence[Dict[str, Any]],
    verification: Optional[VerificationReport] = None,
    da: Optional[Dict[str, Any]] = None,
    portfolio_exposure: float = 0.0,
) -> FinalInvestigationReport:
    da = da or {}
    lead = hypotheses[0] if hypotheses else {}
    alt = hypotheses[1] if len(hypotheses) > 1 else {}
    strength = str(
        (verification.conclusion_strength if verification else "")
        or lead.get("support_status")
        or "UNCERTAIN"
    )
    if strength not in {
        "ROBUST",
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "UNCERTAIN",
        "INSUFFICIENT_EVIDENCE",
        "CONTRADICTED",
    }:
        strength = assess_conclusion_strength(
            lead,
            verification.claim_checks[0] if verification and verification.claim_checks else None,
            has_primary=_has_primary(evidence, lead),
            challenge_severity=str(da.get("challenge_severity") or ""),
        )
    gaps = (
        verification.evidence_gaps
        if verification
        else build_evidence_gap_report(evidence)
    )
    summary = (
        verification.summary
        if verification
        else build_verification_summary(
            verification.claim_checks if verification else [],
            evidence,
            strength,
            gaps,
            da_severity=str(da.get("challenge_severity") or ""),
        )
    )
    summary.devil_advocate_severity = str(da.get("challenge_severity") or summary.devil_advocate_severity)
    summary.final_conclusion = display_conclusion(strength)
    summary.conclusion_strength = strength  # type: ignore[assignment]
    summary.confidence = confidence_label(strength)
    why = str(lead.get("statement") or _INSUFFICIENT_CAUSE)
    if strength == "INSUFFICIENT_EVIDENCE":
        why = _INSUFFICIENT_CAUSE
    supporting = [
        str((evidence_graph._at(evidence, i) or {}).get("title") or f"evidence[{i}]")
        for i in (lead.get("supporting_indices") or lead.get("evidence_indices") or [])
    ]
    contradicting = [
        str((evidence_graph._at(evidence, i) or {}).get("title") or f"evidence[{i}]")
        for i in (lead.get("contradicting_indices") or [])
    ]
    contradicting.extend(list(da.get("contradictory_evidence") or [])[:4])
    sources = []
    for item in evidence:
        url = str(item.get("source_url") or "").strip()
        title = str(item.get("title") or "").strip()
        if url:
            sources.append(f"{title} — {url}" if title else url)
        elif title:
            sources.append(title)
    exposure = (
        f"Portfolio exposure on this name is {portfolio_exposure:.0%} of the session snapshot."
        if portfolio_exposure
        else "Portfolio impact is informational only; this is not a trade recommendation."
    )
    assessment = why
    if strength == "INSUFFICIENT_EVIDENCE":
        assessment = _INSUFFICIENT_CAUSE
    elif strength in {"PARTIALLY_SUPPORTED", "UNCERTAIN"}:
        assessment = (
            f"{why} Confidence is {confidence_label(strength)}; "
            "the investigation does not treat this as proven."
        )
    return FinalInvestigationReport(
        what_happened=move_line,
        why_it_likely_happened=why,
        strongest_alternative=str(alt.get("statement") or ""),
        supporting_evidence=list(dict.fromkeys(supporting))[:8],
        contradicting_evidence=list(dict.fromkeys(contradicting))[:8],
        verification=summary,
        devils_advocate={
            "challenge_severity": da.get("challenge_severity") or "",
            "recommendation": da.get("recommendation") or "",
            "strongest_counterargument": da.get("strongest_counterargument")
            or da.get("counterargument")
            or "",
            "outcome": da.get("outcome") or "",
        },
        evidence_gaps=list(gaps.gaps),
        portfolio_impact=exposure,
        final_assessment=assessment[:2000],
        conclusion_strength=strength,  # type: ignore[arg-type]
        confidence=confidence_label(strength),
        sources=sources[:16],
        trading_advice="",
    )


def _usable_item(item: Dict[str, Any]) -> bool:
    return evidence_graph._usable_evidence(item)


def _fabricated_attribution(statement: str, corpus: str) -> bool:
    """True when the statement attributes words that are not in retrieved evidence."""
    snippets: List[str] = []
    for match in _SAID_RE.finditer(statement or ""):
        snippets.append(match.group(2))
    for match in _QUOTED_RE.finditer(statement or ""):
        snippets.append(match.group(1))
    for snippet in snippets:
        tokens = [t for t in re.findall(r"[a-z0-9]{4,}", snippet.lower()) if t not in {"said", "that", "this"}]
        if tokens and not any(tok in corpus for tok in tokens[:4]):
            return True
    return False


def _causal_core(statement: str) -> str:
    text = (statement or "").strip().rstrip(".")
    text = re.sub(
        r"^(guidance concerns|margin guidance|the (move|decline|drop))\s+"
        r"(caused|drove|contributed to)\s+",
        "Guidance concerns ",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\s+(caused|drove)\s+(the\s+)?(decline|drop|move).*$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\s+(likely )?contributed to the decline$",
        "",
        text,
        flags=re.I,
    )
    text = text.strip() or "The leading hypothesis"
    if text[0].islower():
        text = text[0].upper() + text[1:]
    return text[:240]


def _alternative_title(hypotheses: Sequence[Dict[str, Any]], index: int) -> str:
    if index != 0 or len(hypotheses) < 2:
        return ""
    return str(hypotheses[1].get("statement") or "").strip()


def _looks_official(host: str) -> bool:
    host = (host or "").lower()
    if host.endswith(".gov") or host.endswith("sec.gov"):
        return True
    bare = host[4:] if host.startswith("www.") else host
    return bare.startswith("investor.") or bare.startswith("ir.") or "q4cdn" in bare


def _item_temporal(
    item: Optional[Dict[str, Any]],
    event_time: Optional[datetime],
) -> str:
    if not item:
        return ""
    payload = item.get("raw_payload") or {}
    prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    labeled = str(prov.get("temporal_relevance") or "")
    if labeled in {"PRE_EVENT", "EVENT_TIME", "POST_EVENT"}:
        return labeled
    return str(evidence_graph.graph_temporal(item, event_time) or "")


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _has_primary(evidence: Sequence[Dict[str, Any]], lead: Dict[str, Any]) -> bool:
    for idx in lead.get("supporting_indices") or lead.get("evidence_indices") or []:
        item = evidence_graph._at(evidence, idx)
        if not item:
            continue
        if evidence_graph.source_class(item) == "PRIMARY":
            return True
        if hypothesis_engine.source_quality(item) == "PRIMARY":
            return True
    return False

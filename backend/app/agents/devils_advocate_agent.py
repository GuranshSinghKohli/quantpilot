"""PRD v3 Phase 9 — Devil's Advocate: attempt to falsify the leading hypothesis."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.models.verification_schemas import VerificationReport
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot
from app.services import verification_engine

logger = get_logger("devils_advocate")


async def stress_test_leading(
    move: MoveSnapshot,
    hypotheses: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    hypothesis_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stress-test hypothesis rank #1.

    Returns adjusted hypotheses plus outcome metadata:
      outcome: held | confidence_cut | demoted
      counterargument: str
      leading_weakened: bool
      reversal: bool  (leading hypothesis changed)
    """
    if not hypotheses:
        return {
            "hypotheses": [],
            "outcome": "held",
            "counterargument": "",
            "leading_weakened": False,
            "reversal": False,
            "notes": ["No leading hypothesis to stress-test."],
        }

    ordered = sorted(
        [dict(h) for h in hypotheses],
        key=lambda h: (-float(h.get("weight") or 0), -float(h.get("confidence_score") or 0)),
    )
    leading = ordered[0]
    fallback = _heuristic_da(move, ordered, evidence, hypothesis_context)

    payload = {
        "ticker": move.ticker,
        "move_pct": move.move_pct,
        "window_label": move.window_label,
        "event": (hypothesis_context or {}).get("event") or {},
        "market_context": (hypothesis_context or {}).get("market_context") or {},
        "leading": {
            "statement": leading.get("statement"),
            "stance": leading.get("stance"),
            "weight": leading.get("weight"),
            "evidence_indices": leading.get("evidence_indices") or [],
            "support_status": leading.get("support_status"),
            "claim_type": leading.get("claim_type"),
        },
        "competitors": [
            {
                "index": i,
                "statement": h.get("statement"),
                "stance": h.get("stance"),
                "weight": h.get("weight"),
            }
            for i, h in enumerate(ordered[1:], start=1)
        ],
        "evidence": [
            {
                "index": i,
                "source_type": e.get("source_type"),
                "title": e.get("title"),
                "excerpt": (e.get("excerpt") or "")[:500],
            }
            for i, e in enumerate(evidence)
        ],
        "hypothesis_context": hypothesis_context or {},
    }

    result = await call_openai_json(
        system_prompt=(
            "You are QuantPilot's Devil's Advocate Agent. ATTACK THE LEADING EXPLANATION. "
            "Your job is to try to FALSIFY the leading explanation using the same evidence pool. "
            "Respond ONLY with JSON: "
            '{"counterargument": string, "materially_weakened": boolean, '
            '"confidence_delta": number between -0.4 and 0, '
            '"promote_competitor_index": int|null, '
            '"overweighted_evidence": string, "ignored_evidence": [string, ...], '
            '"questionable_assumptions": [string, ...], '
            '"alternative_more_plausible": boolean, '
            '"alternative_strength": string, '
            '"additional_evidence_needed": [string, ...], '
            '"notes": [string, ...]}. '
            "Answer explicitly: (1) strongest argument against the lead; "
            "(2) contradicting evidence; (3) assumptions being made; "
            "(4) over-weighted evidence; (5) ignored evidence; "
            "(6) whether the strongest alternative is more plausible; "
            "(7) what additional evidence would change the conclusion. "
            "Use hypothesis_context (event, leading vs alternative, supporting vs contradicting, "
            "gaps, source quality, temporal labels, market context, verification_report). "
            "Look for cherry-picking, causal overreach, temporal leakage, "
            "and treating a reported fact as proof of causation. "
            "promote_competitor_index refers to competitors list index (1..n) to promote "
            "to #1 when the leading hypothesis is demoted; null to keep leading with cut confidence. "
            "Be rigorous but fair — do not invent evidence. Do not report fake probabilities."
        ),
        user_prompt=f"Stress-test the leading hypothesis:\n{json.dumps(payload, default=str)}",
        fallback={
            "counterargument": fallback["counterargument"],
            "materially_weakened": fallback["leading_weakened"],
            "confidence_delta": -0.15 if fallback["leading_weakened"] else 0.0,
            "promote_competitor_index": 1 if fallback["outcome"] == "demoted" and len(ordered) > 1 else None,
            "notes": fallback["notes"],
        },
    )

    counter = str(result.get("counterargument") or fallback["counterargument"])[:4000]
    weakened = bool(result.get("materially_weakened"))
    try:
        delta = float(result.get("confidence_delta") or 0.0)
    except (TypeError, ValueError):
        delta = -0.15 if weakened else 0.0
    delta = max(-0.4, min(0.0, delta))

    promote_idx = result.get("promote_competitor_index")
    try:
        promote_idx = int(promote_idx) if promote_idx is not None else None
    except (TypeError, ValueError):
        promote_idx = None

    adjusted = [dict(h) for h in ordered]
    adjusted[0]["devil_advocate_notes"] = counter
    reversal = False
    outcome = "held"

    if weakened and promote_idx and 1 <= promote_idx < len(adjusted):
        # Demote leading; promote competitor.
        old_lead = adjusted.pop(0)
        old_lead["weight"] = max(0.05, float(old_lead.get("weight") or 0.3) + delta)
        old_lead["confidence_score"] = max(
            0.05, float(old_lead.get("confidence_score") or 0.3) + delta
        )
        old_lead["devil_advocate_notes"] = counter
        promoted = adjusted.pop(promote_idx - 1)
        promoted["weight"] = max(
            float(promoted.get("weight") or 0.3),
            float(old_lead.get("weight") or 0.3) + 0.05,
        )
        promoted["devil_advocate_notes"] = (
            f"Promoted after Devil's Advocate overturned prior lead. {counter}"
        )[:4000]
        adjusted = [promoted, old_lead] + adjusted
        outcome = "demoted"
        reversal = True
    elif weakened:
        adjusted[0]["weight"] = max(0.05, float(adjusted[0].get("weight") or 0.4) + delta)
        adjusted[0]["confidence_score"] = max(
            0.05, float(adjusted[0].get("confidence_score") or 0.4) + delta
        )
        # Boost next competitor slightly when confidence is cut.
        if len(adjusted) > 1:
            adjusted[1]["weight"] = min(
                0.95, float(adjusted[1].get("weight") or 0.2) + abs(delta) * 0.5
            )
        outcome = "confidence_cut"
    else:
        if not adjusted[0].get("devil_advocate_notes"):
            adjusted[0]["devil_advocate_notes"] = counter

    adjusted = _renormalize(adjusted)
    for i, h in enumerate(adjusted, start=1):
        h["rank"] = i

    notes = []
    for n in result.get("notes") or fallback["notes"]:
        if isinstance(n, str) and n.strip():
            notes.append(n.strip())

    report = None
    raw_report = (hypothesis_context or {}).get("verification_report")
    if isinstance(raw_report, dict):
        try:
            report = VerificationReport.model_validate(raw_report)
        except Exception:
            report = None
    structured = verification_engine.build_devils_advocate_result(
        adjusted,
        evidence,
        da_raw={
            **result,
            "outcome": outcome,
            "counterargument": counter,
            "notes": notes,
            "additional_evidence_needed": result.get("additional_evidence_needed")
            or fallback.get("additional_evidence_needed"),
            "ignored_evidence": result.get("ignored_evidence")
            or fallback.get("ignored_evidence"),
        },
        verification_report=report,
        hypothesis_context=hypothesis_context,
    )
    log_event(
        logger,
        logging.INFO,
        "Devil's Advocate pass complete",
        ticker=move.ticker,
        outcome=outcome,
        reversal=reversal,
        leading_weakened=weakened,
        challenge_severity=structured.challenge_severity,
        recommendation=structured.recommendation,
    )
    return {
        "hypotheses": adjusted,
        "outcome": outcome,
        "counterargument": structured.strongest_counterargument or counter,
        "leading_weakened": weakened,
        "reversal": reversal,
        "notes": notes[:8],
        "confidence_delta": delta,
        "overweighted_evidence": structured.overweighted_evidence,
        "ignored_evidence": structured.ignored_evidence,
        "alternative_more_plausible": bool(result.get("alternative_more_plausible")),
        "additional_evidence_needed": structured.additional_evidence_needed,
        "hypothesis_context": hypothesis_context or {},
        "target_hypothesis_id": structured.target_hypothesis_id,
        "strongest_counterargument": structured.strongest_counterargument,
        "contradictory_evidence": structured.contradictory_evidence,
        "questionable_assumptions": structured.questionable_assumptions,
        "alternative_hypothesis_id": structured.alternative_hypothesis_id,
        "alternative_strength": structured.alternative_strength,
        "challenge_severity": structured.challenge_severity,
        "recommendation": structured.recommendation,
        "devils_advocate_result": structured.model_dump(),
    }


def _renormalize(hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = sum(float(h.get("weight") or 0) for h in hypotheses) or 0.0
    if total <= 0:
        equal = 1.0 / len(hypotheses)
        for h in hypotheses:
            h["weight"] = round(equal, 4)
        return hypotheses
    for h in hypotheses:
        h["weight"] = round(float(h.get("weight") or 0) / total, 4)
    return hypotheses


def _heuristic_da(
    move: MoveSnapshot,
    ordered: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
    hypothesis_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    leading = ordered[0]
    news = [e for e in evidence if e.get("source_type") == "news"]
    filings = [e for e in evidence if e.get("source_type") in ("filing", "ir_page")]
    stance = str(leading.get("stance") or "")

    weakened = False
    counter = (
        "Alternative explanations remain plausible; treat the lead hypothesis as provisional."
    )
    outcome = "held"

    if stance == "supports_move" and not news and not filings:
        weakened = True
        counter = (
            "Leading company-specific story lacks news/filing support; "
            "sector/market beta may explain the print."
        )
        outcome = "demoted" if len(ordered) > 1 else "confidence_cut"
    elif stance == "supports_move" and len(news) == 1 and abs(move.move_pct or 0) < 2.0:
        weakened = True
        counter = (
            "Move is modest relative to a single thin headline — "
            "idiosyncratic attribution may be overstated."
        )
        outcome = "confidence_cut"
    elif stance == "market_noise" and (news or filings):
        weakened = True
        counter = (
            "Primary-source / news items exist that could support a company-specific driver; "
            "market-noise lead may be too dismissive."
        )
        outcome = "confidence_cut"

    notes = ["Heuristic Devil's Advocate fallback (no LLM)."]
    ctx = hypothesis_context or {}
    extra_needed: List[str] = []
    if ctx.get("cherry_pick_risk"):
        notes.append(
            "Cherry-picking risk: contradictions exist without a contradicted claim."
        )
    if ctx.get("temporal_leakage"):
        notes.append("Post-event evidence must not silently prove causation.")
    gaps = ctx.get("evidence_gaps") or []
    if gaps:
        notes.append(f"Evidence gap: {gaps[0]}")
        extra_needed.extend(str(g) for g in gaps[:3])
    if ctx.get("evidence_conflicts"):
        notes.append("Contradicting evidence is present and must be addressed.")
    if weakened and not extra_needed:
        extra_needed.append(
            "Primary company filing or IR commentary that could confirm or refute the lead."
        )
    return {
        "hypotheses": ordered,
        "outcome": outcome,
        "counterargument": counter,
        "leading_weakened": weakened,
        "reversal": outcome == "demoted",
        "notes": notes[:8],
        "additional_evidence_needed": extra_needed,
        "ignored_evidence": [
            str(row.get("title") or "")
            for row in (ctx.get("contradicting_evidence") or [])
            if isinstance(row, dict)
        ],
    }

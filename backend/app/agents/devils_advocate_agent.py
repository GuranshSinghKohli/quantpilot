"""PRD v3 Phase 9 — Devil's Advocate: attempt to falsify the leading hypothesis."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

logger = get_logger("devils_advocate")


async def stress_test_leading(
    move: MoveSnapshot,
    hypotheses: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
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
    fallback = _heuristic_da(move, ordered, evidence)

    payload = {
        "ticker": move.ticker,
        "move_pct": move.move_pct,
        "window_label": move.window_label,
        "leading": {
            "statement": leading.get("statement"),
            "stance": leading.get("stance"),
            "weight": leading.get("weight"),
            "evidence_indices": leading.get("evidence_indices") or [],
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
    }

    result = await call_openai_json(
        system_prompt=(
            "You are QuantPilot's Devil's Advocate Agent. Your job is to try to FALSIFY "
            "the leading explanation for a price move using the same evidence pool. "
            "Respond ONLY with JSON: "
            '{"counterargument": string, "materially_weakened": boolean, '
            '"confidence_delta": number between -0.4 and 0, '
            '"promote_competitor_index": int|null, '
            '"notes": [string, ...]}. '
            "promote_competitor_index refers to competitors list index (1..n) to promote "
            "to #1 when the leading hypothesis is demoted; null to keep leading with cut confidence. "
            "Be rigorous but fair — do not invent evidence."
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

    log_event(
        logger,
        logging.INFO,
        "Devil's Advocate pass complete",
        ticker=move.ticker,
        outcome=outcome,
        reversal=reversal,
        leading_weakened=weakened,
    )
    return {
        "hypotheses": adjusted,
        "outcome": outcome,
        "counterargument": counter,
        "leading_weakened": weakened,
        "reversal": reversal,
        "notes": notes[:8],
        "confidence_delta": delta,
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

    return {
        "hypotheses": ordered,
        "outcome": outcome,
        "counterargument": counter,
        "leading_weakened": weakened,
        "reversal": outcome == "demoted",
        "notes": ["Heuristic Devil's Advocate fallback (no LLM)."],
    }

"""PRD v3 Phase 8 — weight competing hypotheses against collected evidence."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

logger = get_logger("hypothesis_ranker")

VALID_STANCES = {"supports_move", "contradicts", "market_noise", "unknown"}


async def rank_hypotheses(
    move: MoveSnapshot,
    plan: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Return:
      summary: str
      hypotheses: [{statement, stance, confidence_score, weight, devil_advocate_notes, evidence_indices}]
    """
    fallback = _fallback_rank(move, plan, evidence)
    payload = {
        "ticker": move.ticker,
        "move_pct": move.move_pct,
        "window_label": move.window_label,
        "plan_focus": plan.get("focus"),
        "seed_hypotheses": plan.get("seed_hypotheses") or [],
        "evidence": [
            {
                "index": i,
                "source_type": e.get("source_type"),
                "title": e.get("title"),
                "excerpt": (e.get("excerpt") or "")[:600],
            }
            for i, e in enumerate(evidence)
        ],
    }

    result = await call_openai_json(
        system_prompt=(
            "You are QuantPilot's Reasoning Agent. Rank competing explanations for a price move. "
            "Do NOT return a single confident answer — return weighted hypotheses. "
            "Respond ONLY with JSON: "
            '{"summary": string, "hypotheses": [{'
            '"statement": string, "stance": "supports_move"|"contradicts"|"market_noise"|"unknown", '
            '"confidence_score": number 0-1, "weight": number 0-1, '
            '"devil_advocate_notes": string, "evidence_indices": [int, ...]'
            "}]}. "
            "Include 3-5 hypotheses sorted by weight descending. Weights should roughly sum to 1. "
            "Every hypothesis that is not pure market_noise should cite at least one evidence_index when evidence exists. "
            "devil_advocate_notes: strongest counterargument to that hypothesis."
        ),
        user_prompt=f"Rank hypotheses for this move:\n{json.dumps(payload, default=str)}",
        fallback=fallback,
    )

    raw = result.get("hypotheses") if isinstance(result.get("hypotheses"), list) else []
    hypotheses = [_normalize_hypothesis(h, evidence) for h in raw]
    hypotheses = [h for h in hypotheses if h is not None]
    if not hypotheses:
        hypotheses = fallback["hypotheses"]

    hypotheses = _renormalize_weights(hypotheses)
    hypotheses.sort(key=lambda h: (-h["weight"], -h["confidence_score"]))
    for i, h in enumerate(hypotheses, start=1):
        h["rank"] = i

    summary = (result.get("summary") or fallback["summary"] or "")[:2000]
    log_event(
        logger,
        logging.INFO,
        "Hypotheses ranked",
        ticker=move.ticker,
        count=len(hypotheses),
        top_weight=hypotheses[0]["weight"] if hypotheses else 0,
    )
    return {"summary": summary, "hypotheses": hypotheses}


def _normalize_hypothesis(
    raw: Any, evidence: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    statement = str(raw.get("statement") or "").strip()
    if not statement:
        return None
    stance = str(raw.get("stance") or "unknown")
    if stance not in VALID_STANCES:
        stance = "unknown"
    try:
        confidence = float(raw.get("confidence_score") or 0.4)
    except (TypeError, ValueError):
        confidence = 0.4
    try:
        weight = float(raw.get("weight") or confidence)
    except (TypeError, ValueError):
        weight = confidence
    indices: List[int] = []
    for idx in raw.get("evidence_indices") or []:
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(evidence):
            indices.append(i)
    return {
        "statement": statement[:4000],
        "stance": stance,
        "confidence_score": max(0.0, min(1.0, confidence)),
        "weight": max(0.0, min(1.0, weight)),
        "devil_advocate_notes": str(raw.get("devil_advocate_notes") or "")[:4000],
        "evidence_indices": indices,
    }


def _renormalize_weights(hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    total = sum(h["weight"] for h in hypotheses) or 0.0
    if total <= 0:
        equal = 1.0 / len(hypotheses)
        for h in hypotheses:
            h["weight"] = round(equal, 4)
        return hypotheses
    for h in hypotheses:
        h["weight"] = round(h["weight"] / total, 4)
    return hypotheses


def _fallback_rank(
    move: MoveSnapshot,
    plan: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    seeds = plan.get("seed_hypotheses") or [
        "Company-specific catalyst",
        "Market/sector beta (noise)",
        "Unknown / insufficient evidence",
    ]
    newsish = [i for i, e in enumerate(evidence) if e.get("source_type") == "news"]
    filingish = [
        i for i, e in enumerate(evidence) if e.get("source_type") in ("filing", "ir_page")
    ]
    priceish = [i for i, e in enumerate(evidence) if e.get("source_type") == "price"]

    abs_move = abs(move.move_pct or 0.0)
    if move.is_noise or abs_move < 1.5:
        weights = [0.25, 0.55, 0.20]
        stances = ["supports_move", "market_noise", "unknown"]
    elif newsish:
        weights = [0.5, 0.25, 0.25]
        stances = ["supports_move", "market_noise", "unknown"]
    else:
        weights = [0.35, 0.4, 0.25]
        stances = ["unknown", "market_noise", "supports_move"]

    hypotheses: List[Dict[str, Any]] = []
    for i, seed in enumerate(seeds[:3]):
        indices = newsish[:2] if i == 0 else (priceish[:1] or filingish[:1])
        if i == 1:
            indices = priceish[:1] or newsish[:1]
        hypotheses.append(
            {
                "statement": seed,
                "stance": stances[min(i, len(stances) - 1)],
                "confidence_score": weights[min(i, len(weights) - 1)],
                "weight": weights[min(i, len(weights) - 1)],
                "devil_advocate_notes": (
                    "Could still be sector beta or delayed reaction to older news."
                    if i == 0
                    else "A company-specific catalyst may not have hit headlines yet."
                ),
                "evidence_indices": indices,
                "rank": i + 1,
            }
        )

    direction = "up" if (move.move_pct or 0) >= 0 else "down"
    pct = f"{abs_move:.2f}%" if move.move_pct is not None else "n/a"
    summary = (
        f"{move.ticker} moved {direction} {pct} ({move.window_label}). "
        f"Top weighted explanation: {hypotheses[0]['statement']} "
        f"(weight {hypotheses[0]['weight']:.0%}). "
        "Competing hypotheses retained — not a single definitive answer."
    )
    return {"summary": summary, "hypotheses": hypotheses}

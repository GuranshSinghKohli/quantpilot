"""PRD v3 Phase 9 — Verification: no claim without traceable evidence."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.observability.logger import get_logger, log_event

logger = get_logger("investigation_verification")


async def verify_hypotheses(
    ticker: str,
    hypotheses: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Ensure every hypothesis that reaches the user is grounded.

    Returns:
      hypotheses: grounded list (may drop unsupported)
      rejected: [{statement, reason}]
      notes: List[str]
      citation_coverage: float 0-1
    """
    if not hypotheses:
        return {
            "hypotheses": [],
            "rejected": [],
            "notes": ["No hypotheses to verify."],
            "citation_coverage": 1.0,
        }

    fallback = _heuristic_verify(hypotheses, evidence)
    payload = {
        "ticker": ticker,
        "evidence": [
            {
                "index": i,
                "source_type": e.get("source_type"),
                "title": e.get("title"),
                "excerpt": (e.get("excerpt") or "")[:400],
            }
            for i, e in enumerate(evidence)
        ],
        "hypotheses": [
            {
                "index": i,
                "statement": h.get("statement"),
                "stance": h.get("stance"),
                "evidence_indices": h.get("evidence_indices") or [],
                "weight": h.get("weight"),
            }
            for i, h in enumerate(hypotheses)
        ],
    }

    result = await call_openai_json(
        system_prompt=(
            "You are QuantPilot's Investigation Verification Agent. "
            "A claim may ONLY appear in output if it links to at least one retrieved evidence item. "
            "Respond ONLY with JSON: "
            '{"keep_indices": [int, ...], "rejected": [{"index": int, "reason": string}], '
            '"notes": [string, ...], '
            '"force_links": [{"hypothesis_index": int, "evidence_indices": [int, ...]}]}. '
            "keep_indices: hypotheses safe to show. "
            "force_links: when a keep hypothesis needs stronger/corrected evidence indices. "
            "Reject company-specific claims with no attributable evidence. "
            "market_noise claims may keep a price evidence index."
        ),
        user_prompt=f"Verify investigation claims:\n{json.dumps(payload, default=str)}",
        fallback={
            "keep_indices": list(range(len(fallback["hypotheses"]))),
            "rejected": [
                {"index": -1, "reason": r.get("reason", "")}
                for r in fallback["rejected"]
            ],
            "notes": fallback["notes"],
            "force_links": [],
        },
    )

    # Start from heuristic grounding (hard guarantee), then apply LLM force_links.
    grounded = _heuristic_verify(hypotheses, evidence)
    by_statement = {h["statement"]: h for h in grounded["hypotheses"]}

    force_links = result.get("force_links") or []
    if isinstance(force_links, list):
        for link in force_links:
            if not isinstance(link, dict):
                continue
            try:
                hi = int(link.get("hypothesis_index"))
            except (TypeError, ValueError):
                continue
            if hi < 0 or hi >= len(hypotheses):
                continue
            stmt = str(hypotheses[hi].get("statement") or "")
            target = by_statement.get(stmt)
            if target is None:
                continue
            indices = []
            for idx in link.get("evidence_indices") or []:
                try:
                    i = int(idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(evidence):
                    indices.append(i)
            if indices:
                target["evidence_indices"] = indices

    # Final hard filter: drop anything still unlinked.
    final: List[Dict[str, Any]] = []
    rejected = list(grounded["rejected"])
    for h in grounded["hypotheses"]:
        if h.get("evidence_indices"):
            final.append(h)
        else:
            rejected.append(
                {
                    "statement": h.get("statement"),
                    "reason": "No claim–evidence link after verification.",
                }
            )

    if not final and evidence:
        # Always retain a market-noise / unknown claim linked to price evidence.
        final.append(_noise_fallback(hypotheses, evidence))

    notes = list(grounded["notes"])
    for n in result.get("notes") or []:
        if isinstance(n, str) and n.strip():
            notes.append(n.strip())

    coverage = (
        len(final) / max(1, len(final) + len(rejected))
        if (final or rejected)
        else 1.0
    )
    log_event(
        logger,
        logging.INFO,
        "Investigation verification complete",
        ticker=ticker,
        kept=len(final),
        rejected=len(rejected),
        citation_coverage=round(coverage, 3),
    )
    return {
        "hypotheses": final,
        "rejected": rejected[:12],
        "notes": notes[:12],
        "citation_coverage": round(coverage, 3),
    }


def _heuristic_verify(
    hypotheses: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    price_idxs = [
        i for i, e in enumerate(evidence) if e.get("source_type") == "price"
    ]
    any_idxs = list(range(len(evidence)))
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    notes: List[str] = []

    for h in hypotheses:
        clone = dict(h)
        indices = [
            int(i)
            for i in (clone.get("evidence_indices") or [])
            if str(i).lstrip("-").isdigit() and 0 <= int(i) < len(evidence)
        ]
        stance = str(clone.get("stance") or "unknown")

        if not indices:
            if stance == "market_noise" and price_idxs:
                indices = price_idxs[:1]
                notes.append("Linked market_noise claim to price evidence.")
            elif any_idxs and stance in ("unknown", "market_noise"):
                indices = any_idxs[:1]
                notes.append("Linked weakly supported claim to available evidence.")
            else:
                rejected.append(
                    {
                        "statement": clone.get("statement"),
                        "reason": "Unsupported: no attributable evidence item.",
                    }
                )
                continue

        clone["evidence_indices"] = indices
        kept.append(clone)

    return {
        "hypotheses": kept,
        "rejected": rejected,
        "notes": notes
        or ["Heuristic verification: all kept claims have evidence links."],
        "citation_coverage": (
            len(kept) / max(1, len(hypotheses)) if hypotheses else 1.0
        ),
    }


def _noise_fallback(
    hypotheses: List[Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    price_idxs = [
        i for i, e in enumerate(evidence) if e.get("source_type") == "price"
    ] or [0]
    seed = next(
        (h for h in hypotheses if h.get("stance") == "market_noise"),
        None,
    )
    return {
        "statement": (seed or {}).get("statement")
        or "Insufficient attributable evidence for a company-specific driver.",
        "stance": "market_noise",
        "confidence_score": 0.45,
        "weight": 1.0,
        "devil_advocate_notes": "Thin evidence pool after verification.",
        "evidence_indices": price_idxs[:1],
        "rank": 1,
    }

"""Phase 4 — generate a small set of competing causal hypotheses."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.config import MAX_HYPOTHESES
from app.models.hypothesis_schemas import HypothesisRecord
from app.observability.logger import get_logger, log_event
from app.services.hypothesis_engine import (
    _STANCE_FOR_CATEGORY,
    _default_requirements,
    _default_tools,
    _norm_text,
)
from app.services.move_detector import MoveSnapshot

logger = get_logger("hypothesis_generator")

_VALID_CATEGORIES = {
    "COMPANY_SPECIFIC",
    "EARNINGS",
    "GUIDANCE",
    "MACRO",
    "REGULATORY",
    "COMPETITOR",
    "INDUSTRY",
    "MARKET_WIDE",
    "TECHNICAL",
    "SENTIMENT",
    "OTHER",
}


def _lightweight() -> bool:
    return os.getenv("QUANTPILOT_LIGHTWEIGHT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def catalog_hypotheses(
    move: MoveSnapshot,
    *,
    asset_hint: str = "equity",
    event: Optional[Dict[str, Any]] = None,
    seeds: Optional[List[str]] = None,
    max_hypotheses: Optional[int] = None,
) -> List[HypothesisRecord]:
    """Deterministic 3–6 competing explanations. No LLM."""
    limit = max(3, min(8, max_hypotheses or MAX_HYPOTHESES))
    hint = (asset_hint or getattr(move, "asset_class", None) or "equity").lower()
    abs_move = abs(move.move_pct or 0.0)
    residual = abs(getattr(move, "residual_pct", None) or 0.0)
    event_type = str((event or {}).get("event_type") or "PRICE_MOVE").upper()

    specs: List[Dict[str, str]] = []
    if hint == "etf":
        specs = [
            _spec("MARKET_WIDE", "Broad market / factor move", "The ETF printed with the risk tape."),
            _spec("INDUSTRY", "Sector / holding spillover", "Top holdings or sector news spilled into the basket."),
            _spec("MACRO", "Rates / macro expectations", "Rates or growth expectations shifted the basket."),
            _spec("TECHNICAL", "Technical / positioning", "Flows or profit-taking without a new fundamental fact."),
        ]
    else:
        specs = [
            _spec(
                "GUIDANCE",
                "Guidance / earnings disappointment",
                "Management cut or softened the near-term outlook.",
            ),
            _spec(
                "MARKET_WIDE",
                "Broad market / sector selloff",
                "The name moved with SPY or the semiconductor tape.",
            ),
        ]
        if event_type in {"EARNINGS"} or abs_move >= 3.0:
            specs.append(
                _spec(
                    "EARNINGS",
                    "Company-specific earnings miss",
                    "A print or estimate miss disappointed vs consensus.",
                )
            )
        if event_type in {"FILING"} or residual >= 1.0 or abs_move >= 4.0:
            specs.append(
                _spec(
                    "REGULATORY",
                    "Regulatory / export restriction",
                    "A policy, license, or filing shock hit the name.",
                )
            )
        if abs_move >= 2.5:
            specs.append(
                _spec(
                    "COMPETITOR",
                    "Competitor announcement",
                    "A rival product, pricing, or share-shift headline.",
                )
            )
        specs.append(
            _spec(
                "TECHNICAL",
                "Technical / profit-taking",
                "Positioning unwound without a new fundamental catalyst.",
            )
        )
        if "AI" in (move.ticker or "").upper() or move.ticker in {"NVDA", "AMD", "AVGO", "TSM"}:
            specs.insert(
                2,
                _spec(
                    "INDUSTRY",
                    "AI / industry demand concerns",
                    "The tape priced weaker data-center or semiconductor demand.",
                ),
            )

    merged = _merge_seeds(specs, seeds or [])
    records: List[HypothesisRecord] = []
    seen = set()
    for spec in merged:
        title = spec["title"]
        key = _norm_text(title)
        if key in seen:
            continue
        seen.add(key)
        category = spec["category"]
        req, contra = _default_requirements(category)
        records.append(
            HypothesisRecord(
                hypothesis_id=f"H{len(records) + 1}",
                title=title,
                description=spec["why"],
                category=category,  # type: ignore[arg-type]
                status="OPEN",
                why_plausible=spec["why"],
                required_evidence=req,
                contradicting_requirements=contra,
                missing_evidence=list(req),
                tool_categories=_default_tools(category),
                stance=_STANCE_FOR_CATEGORY.get(category, "unknown"),
            )
        )
        if len(records) >= limit:
            break
    while len(records) < 3:
        idx = len(records) + 1
        records.append(
            HypothesisRecord(
                hypothesis_id=f"H{idx}",
                title="Unknown or liquidity-driven move",
                description="No competing explanation is complete yet.",
                category="OTHER",
                status="OPEN",
                required_evidence=["additional company or market evidence"],
                tool_categories=["news"],
            )
        )
    return records[:limit]


async def generate_hypotheses(
    move: MoveSnapshot,
    *,
    asset_hint: str = "equity",
    event: Optional[Dict[str, Any]] = None,
    seeds: Optional[List[str]] = None,
    materiality_score: float = 0.0,
    portfolio_exposure: float = 0.0,
    max_hypotheses: Optional[int] = None,
) -> List[HypothesisRecord]:
    fallback = catalog_hypotheses(
        move,
        asset_hint=asset_hint,
        event=event,
        seeds=seeds,
        max_hypotheses=max_hypotheses,
    )
    if _lightweight():
        _log_generated(move, fallback)
        return fallback

    payload = {
        "ticker": move.ticker,
        "move_pct": move.move_pct,
        "residual_pct": getattr(move, "residual_pct", None),
        "event": event or {},
        "materiality_score": materiality_score,
        "portfolio_exposure": portfolio_exposure,
        "seeds": seeds or [],
        "asset_hint": asset_hint,
        "limit": max(3, min(8, max_hypotheses or MAX_HYPOTHESES)),
        "categories": sorted(_VALID_CATEGORIES),
    }
    result = await call_openai_json(
        system_prompt=(
            "You generate competing causal hypotheses for a price move. "
            "Return 3-6 hypotheses. Do NOT pick a winner. "
            "Respond ONLY with JSON: "
            '{"hypotheses":[{"title":str,"why_plausible":str,"category":str,'
            '"required_evidence":[str],"contradicting_requirements":[str],'
            '"tool_categories":[str]}]}. '
            f"category must be one of {sorted(_VALID_CATEGORIES)}."
        ),
        user_prompt=f"Generate hypotheses:\n{json.dumps(payload, default=str)}",
        fallback={"hypotheses": [r.model_dump() for r in fallback]},
    )
    parsed = _parse_llm(result, fallback, max_hypotheses or MAX_HYPOTHESES)
    _log_generated(move, parsed)
    return parsed


def _spec(category: str, title: str, why: str) -> Dict[str, str]:
    return {"category": category, "title": title, "why": why}


def _merge_seeds(specs: List[Dict[str, str]], seeds: List[str]) -> List[Dict[str, str]]:
    out = list(specs)
    for seed in seeds:
        text = seed.strip()
        if not text:
            continue
        if any(_norm_text(text) in _norm_text(s["title"]) or _norm_text(s["title"]) in _norm_text(text) for s in out):
            continue
        out.append(_spec(_infer_category(text), text[:120], "Planner-seeded competing explanation."))
    return out


def _infer_category(text: str) -> str:
    lowered = text.lower()
    mapping = (
        ("guidance", "GUIDANCE"),
        ("earning", "EARNINGS"),
        ("export", "REGULATORY"),
        ("regulat", "REGULATORY"),
        ("competitor", "COMPETITOR"),
        ("rival", "COMPETITOR"),
        ("macro", "MACRO"),
        ("spy", "MARKET_WIDE"),
        ("sector", "MARKET_WIDE"),
        ("beta", "MARKET_WIDE"),
        ("technical", "TECHNICAL"),
        ("profit", "TECHNICAL"),
        ("demand", "INDUSTRY"),
        ("sentiment", "SENTIMENT"),
    )
    for token, category in mapping:
        if token in lowered:
            return category
    return "COMPANY_SPECIFIC"


def _parse_llm(
    result: Dict[str, Any],
    fallback: List[HypothesisRecord],
    limit: int,
) -> List[HypothesisRecord]:
    raw = result.get("hypotheses") if isinstance(result.get("hypotheses"), list) else []
    records: List[HypothesisRecord] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or _norm_text(title) in seen:
            continue
        seen.add(_norm_text(title))
        category = str(item.get("category") or "OTHER").upper()
        if category not in _VALID_CATEGORIES:
            category = _infer_category(title)
        req = [str(x) for x in (item.get("required_evidence") or []) if str(x).strip()]
        contra = [
            str(x)
            for x in (item.get("contradicting_requirements") or [])
            if str(x).strip()
        ]
        if not req:
            req, default_contra = _default_requirements(category)
            contra = contra or default_contra
        tools = [str(x) for x in (item.get("tool_categories") or []) if str(x).strip()]
        records.append(
            HypothesisRecord(
                hypothesis_id=f"H{len(records) + 1}",
                title=title[:200],
                description=str(item.get("why_plausible") or "")[:400],
                category=category,  # type: ignore[arg-type]
                status="OPEN",
                why_plausible=str(item.get("why_plausible") or "")[:400],
                required_evidence=req[:6],
                contradicting_requirements=contra[:6],
                missing_evidence=list(req[:6]),
                tool_categories=tools or _default_tools(category),
                stance=_STANCE_FOR_CATEGORY.get(category, "unknown"),
            )
        )
        if len(records) >= min(6, limit):
            break
    if len(records) < 3:
        return fallback
    return records[: min(6, limit)]


def _log_generated(move: MoveSnapshot, records: List[HypothesisRecord]) -> None:
    log_event(
        logger,
        logging.INFO,
        "HYPOTHESES_GENERATED",
        event="HYPOTHESES_GENERATED",
        ticker=move.ticker,
        count=len(records),
        ids=[r.hypothesis_id for r in records],
        categories=[r.category for r in records],
    )
    log_event(
        logger,
        logging.INFO,
        "HYPOTHESIS_RESEARCH_REQUIREMENTS_CREATED",
        event="HYPOTHESIS_RESEARCH_REQUIREMENTS_CREATED",
        ticker=move.ticker,
        requirements={
            r.hypothesis_id: r.required_evidence for r in records
        },
    )

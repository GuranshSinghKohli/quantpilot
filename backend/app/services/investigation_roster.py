"""Run Earnings / Macro agents on investigation evidence — no investment memo."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.agents.earnings_agent import analyze_earnings
from app.agents.macro_agent import analyze_macro
from app.observability.logger import get_logger, log_event
from app.services.move_detector import MoveSnapshot

logger = get_logger("investigation_roster")


async def run_roster_pass(
    move: MoveSnapshot,
    evidence_rows: List[Dict[str, Any]],
    *,
    hypotheses: Optional[List[Dict[str, Any]]] = None,
    verification_notes: str = "",
    da_outcome: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Light roster expansion for investigations (not the full research graph).

    Reuses Earnings / Macro agents with evidence already collected for the case.
    Does not produce buy/hold/sell recommendations.
    """
    ticker = move.ticker
    headlines = _headlines_from_evidence(evidence_rows)
    filings = _filings_from_evidence(evidence_rows)
    fundamentals = _fundamentals_from_evidence(evidence_rows)
    ir_materials = _ir_from_evidence(evidence_rows)
    news_output = {
        "summary": f"Investigation news scan for {ticker}",
        "sentiment": "mixed",
    }

    earnings: Dict[str, Any] = {}
    macro: Dict[str, Any] = {}

    try:
        earn, _ir = await analyze_earnings(
            ticker,
            fundamentals,
            filings,
            headlines,
        )
        earnings = earn.model_dump()
        if ir_materials and not earnings.get("earnings_summary"):
            earnings["ir_excerpt"] = (ir_materials.get("excerpt") or "")[:500]
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Investigation earnings pass failed",
            ticker=ticker,
            error=str(exc),
        )
        earnings = {"earnings_summary": "Earnings context unavailable.", "tone": "unknown"}

    try:
        mac = await analyze_macro(ticker, headlines, news_output)
        macro = mac.model_dump()
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Investigation macro pass failed",
            ticker=ticker,
            error=str(exc),
        )
        macro = {"macro_summary": "Macro context unavailable.", "relevance": "none"}

    roster = {
        "earnings": earnings,
        "macro": macro,
        "memo": {},
    }
    log_event(
        logger,
        logging.INFO,
        "Investigation roster pass complete",
        ticker=ticker,
        earnings_tone=earnings.get("tone"),
        macro_relevance=macro.get("relevance"),
    )
    return roster


def _headlines_from_evidence(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("source_type") != "news":
            continue
        payload = row.get("raw_payload") or {}
        out.append(
            {
                "title": row.get("title") or payload.get("title") or "",
                "publisher": payload.get("publisher") or "",
                "link": row.get("source_url") or payload.get("link") or "",
            }
        )
    return out[:12]


def _filings_from_evidence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    filings = []
    for row in rows:
        if row.get("source_type") != "filing":
            continue
        payload = row.get("raw_payload") or {}
        filings.append(
            {
                "form_type": payload.get("form_type") or "filing",
                "filing_date": payload.get("filing_date") or "",
                "document_url": row.get("source_url") or "",
            }
        )
    return {"filings": filings}


def _fundamentals_from_evidence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in rows:
        if row.get("source_type") in {"fundamentals", "other"}:
            payload = row.get("raw_payload") or {}
            if isinstance(payload, dict) and (
                payload.get("key_metrics") or payload.get("pe_ratio")
            ):
                return payload if payload.get("key_metrics") else {"key_metrics": payload}
    return {"key_metrics": {}}


def _ir_from_evidence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in rows:
        if row.get("source_type") in {"ir_page", "transcript"}:
            payload = row.get("raw_payload") or {}
            if isinstance(payload, dict) and (
                payload.get("excerpt") or payload.get("pages") or row.get("excerpt")
            ):
                return {
                    "excerpt": row.get("excerpt") or payload.get("excerpt") or "",
                    "sources": payload.get("sources")
                    or ([row.get("source_url")] if row.get("source_url") else []),
                    "pages": payload.get("pages") or [],
                }
    return {}

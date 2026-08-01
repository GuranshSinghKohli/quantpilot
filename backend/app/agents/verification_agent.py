"""Verification Agent — flags unsupported claims before report finalization."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.models.agent_schemas import VerificationAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("verification_agent")


def _fallback(
    metrics: Dict[str, Any],
    sec: Dict[str, Any],
    news: Dict[str, Any],
    earnings: Dict[str, Any],
) -> VerificationAgentOutput:
    verified: List[str] = []
    unsupported: List[str] = []
    notes: List[str] = []

    key_metrics = metrics.get("key_metrics") or {}
    if key_metrics.get("pe_ratio") is not None or metrics.get("pe_ratio") is not None:
        verified.append("Valuation/P/E inputs present from market data.")
    else:
        unsupported.append("P/E or valuation inputs missing from retrieved metrics.")

    if sec.get("filing_summary"):
        verified.append("SEC filing summary available as a primary-source trace.")
    else:
        notes.append("Limited SEC filing text for claim tracing.")

    if news.get("summary"):
        verified.append("News summary present for narrative context.")
    else:
        notes.append("Thin news coverage reduces claim coverage.")

    if earnings.get("earnings_summary"):
        verified.append("Earnings context section available.")
    else:
        notes.append("Earnings context was weak or unavailable.")

    grounded = 0.7 if verified and not unsupported else 0.45
    if unsupported:
        grounded = max(0.2, grounded - 0.2 * len(unsupported))

    return VerificationAgentOutput(
        verified_claims=verified or ["Basic data bundle present."],
        unsupported_claims=unsupported,
        coverage_notes=notes or ["Heuristic verification only (fallback path)."],
        groundedness_score=round(grounded, 3),
        confidence_score=0.5,
    )


async def verify_claims(
    ticker: str,
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    earnings_output: Dict[str, Any],
    macro_output: Dict[str, Any],
    debate_output: Dict[str, Any],
) -> VerificationAgentOutput:
    fallback = _fallback(metrics_output, sec_output, news_output, earnings_output)
    try:
        payload = {
            "ticker": ticker,
            "news_output": news_output,
            "metrics_output": metrics_output,
            "sec_output": sec_output,
            "earnings_output": earnings_output,
            "macro_output": macro_output,
            "debate_output": debate_output,
        }
        result = await call_openai_json(
            system_prompt=(
                "You are a research verification analyst. Separate claims that are "
                "traceable to retrieved inputs (metrics, filings, headlines) from "
                "unsupported or speculative claims in agent narratives. "
                "Do not invent sources. Respond ONLY with JSON: "
                '{"verified_claims": [string,...], "unsupported_claims": [string,...], '
                '"coverage_notes": [string,...], "groundedness_score": number 0-1, '
                '"confidence_score": number 0-1}'
            ),
            user_prompt=(
                f"Verify claim grounding for {ticker} before final report:\n"
                f"{json.dumps(payload, default=str)}"
            ),
            fallback=fallback.model_dump(),
        )
        return VerificationAgentOutput.model_validate(result)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Verification agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback.model_dump()
        fb["error_message"] = str(exc)
        fb["confidence_score"] = 0.0
        return VerificationAgentOutput.model_validate(fb)

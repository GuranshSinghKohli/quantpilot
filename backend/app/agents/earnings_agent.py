"""Earnings Agent — summarizes earnings context from filings, news, and IR materials."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Dict, List, Optional, Tuple

from app.agents.llm import call_openai_json
from app.mcp_client import MCPClientError, call_tool
from app.models.agent_schemas import EarningsAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("earnings_agent")


def _fallback(
    ticker: str,
    metrics: Dict[str, Any],
    filings: Dict[str, Any],
    headlines: List[Dict[str, Any]],
    ir_materials: Optional[Dict[str, Any]] = None,
) -> EarningsAgentOutput:
    pe = (metrics.get("key_metrics") or {}).get("pe_ratio") or metrics.get("pe_ratio")
    filing_list = filings.get("filings") or []
    latest = filing_list[0] if filing_list else {}
    points = []
    if pe is not None:
        points.append(f"Trailing P/E near {pe}.")
    if latest:
        points.append(
            f"Latest filing: {latest.get('form_type', 'N/A')} on {latest.get('filing_date', 'N/A')}."
        )
    earn_news = [
        h.get("title", "")
        for h in headlines[:5]
        if any(k in (h.get("title") or "").lower() for k in ("earn", "eps", "revenue", "guidance"))
    ]
    points.extend(earn_news[:2])

    ir = ir_materials or {}
    sources = ir.get("sources") or []
    if sources:
        points.append(f"IR materials retrieved from {sources[0]}.")
    if not points:
        points.append("Limited earnings-specific signals in available data.")

    has_ir = bool(ir.get("excerpt") or sources)
    summary = (
        f"Earnings context for {ticker} from fundamentals, filings, headlines"
        + (", and investor-relations pages." if has_ir else ".")
    )
    if not has_ir:
        summary += " Browser/IR feed returned no usable pages."

    return EarningsAgentOutput(
        earnings_summary=summary,
        tone="mixed" if earn_news or has_ir else "unknown",
        key_points=points[:5],
        next_catalyst="Watch for the next earnings release and related 8-K/10-Q filings.",
        confidence_score=0.55 if has_ir else 0.45,
        sources=list(sources)[:5],
    )


async def _fetch_ir_via_mcp(ticker: str) -> Dict[str, Any]:
    return await call_tool("get_ir_materials", {"ticker": ticker, "max_pages": 2})


async def analyze_earnings(
    ticker: str,
    metrics_output: Dict[str, Any],
    filings_data: Dict[str, Any],
    news_headlines: List[Dict[str, Any]],
) -> Tuple[EarningsAgentOutput, Dict[str, Any]]:
    ir_materials: Dict[str, Any] = {}
    mcp_error = ""
    try:
        ir_materials = await _fetch_ir_via_mcp(ticker)
    except MCPClientError as exc:
        mcp_error = str(exc)
        log_event(
            logger,
            logging.WARNING,
            "MCP IR materials fetch failed",
            ticker=ticker,
            error=str(exc),
        )

    fallback = _fallback(
        ticker, metrics_output, filings_data, news_headlines, ir_materials
    )
    try:
        payload = {
            "ticker": ticker,
            "metrics_output": metrics_output,
            "recent_filings": (filings_data.get("filings") or [])[:3],
            "headlines": [
                {"title": h.get("title", ""), "publisher": h.get("publisher", "")}
                for h in (news_headlines or [])[:8]
            ],
            "ir_materials": {
                "provider": ir_materials.get("provider"),
                "sources": ir_materials.get("sources") or [],
                "excerpt": (ir_materials.get("excerpt") or "")[:5000],
                "error": ir_materials.get("error") or mcp_error,
            },
        }
        result = await call_openai_json(
            system_prompt=(
                "You are an equity earnings analyst. Infer earnings context from fundamentals, "
                "SEC filings, headlines, and investor-relations page excerpts. "
                "Do not fabricate transcript quotes. Prefer IR excerpts when present. "
                "Respond ONLY with JSON: "
                '{"earnings_summary": string, "tone": "positive"|"mixed"|"negative"|"unknown", '
                '"key_points": [string,...], "next_catalyst": string, '
                '"confidence_score": number 0-1, "sources": [string,...]}'
            ),
            user_prompt=f"Build earnings context for {ticker}:\n{json.dumps(payload, default=str)}",
            fallback=fallback.model_dump(),
        )
        out = EarningsAgentOutput.model_validate(result)
        if not out.sources:
            out.sources = list(ir_materials.get("sources") or [])[:5]
        return out, ir_materials
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Earnings agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback.model_dump()
        fb["error_message"] = str(exc)
        fb["confidence_score"] = 0.0
        return EarningsAgentOutput.model_validate(fb), ir_materials

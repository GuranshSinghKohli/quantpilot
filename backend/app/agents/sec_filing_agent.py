import json
import logging
import traceback
from typing import Any, Dict

from app.agents.llm import call_openai_json
from app.mcp_client import MCPClientError, call_tool
from app.models.agent_schemas import SECFilingAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("sec_filing_agent")


def _fallback(filings_data: Dict[str, Any], error: str = "") -> SECFilingAgentOutput:
    filings = filings_data.get("filings") or []
    latest_type = filings[0].get("form_type", "N/A") if filings else "N/A"
    types = [f.get("form_type", "") for f in filings]
    summary = (
        f"Reviewed {len(filings)} recent SEC filings: {', '.join(types) or 'none'}."
    )
    if error:
        summary = f"{summary} (MCP: {error})"
    return SECFilingAgentOutput(
        filing_summary=summary,
        risk_signals=["Review 10-K risk factors section for full disclosure"],
        latest_filing_type=latest_type,
    )


async def _fetch_filings_via_mcp(ticker: str) -> Dict[str, Any]:
    return await call_tool("get_recent_filings", {"ticker": ticker, "limit": 3})


async def analyze_sec_filings(
    ticker: str, filings_data: Dict[str, Any] = None
) -> SECFilingAgentOutput:
    try:
        mcp_error = ""
        sec_data = filings_data or {}

        try:
            sec_data = await _fetch_filings_via_mcp(ticker)
        except MCPClientError as exc:
            mcp_error = str(exc)
            log_event(
                logger, logging.WARNING, "MCP SEC filings failed", ticker=ticker, error=str(exc)
            )
            if not sec_data:
                fb = _fallback({}, mcp_error)
                return SECFilingAgentOutput(
                    **fb.model_dump(),
                    confidence_score=0.0,
                    error_message=str(exc),
                )

        fallback = _fallback(sec_data, mcp_error)
        payload = json.dumps({"ticker": ticker, "filings_data": sec_data}, default=str)

        result = await call_openai_json(
            system_prompt=(
                "You are an SEC filings analyst. Review recent filing metadata and infer risk signals. "
                "Respond ONLY with valid JSON: "
                '{"filing_summary": string, "risk_signals": [string, ...], "latest_filing_type": string}'
            ),
            user_prompt=f"Analyze SEC filings for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )

        try:
            return SECFilingAgentOutput.model_validate(result)
        except Exception:
            return fallback
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "SEC filing agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = _fallback(filings_data or {}, str(exc))
        return SECFilingAgentOutput(
            **fb.model_dump(),
            confidence_score=0.0,
            error_message=str(exc),
        )

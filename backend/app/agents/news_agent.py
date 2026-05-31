import json
import logging
import traceback
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.mcp_client import MCPClientError, call_tool
from app.models.agent_schemas import NewsAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("news_agent")


def _fallback(headlines: List[Dict[str, Any]], error: str = "") -> NewsAgentOutput:
    titles = [h.get("title", "") for h in headlines[:3] if h.get("title")]
    summary = (
        f"Analyzed {len(headlines)} recent headlines. "
        + (f"Top stories: {'; '.join(titles)}." if titles else "No headlines available.")
    )
    if error:
        summary = f"{summary} (MCP: {error})"
    return NewsAgentOutput(
        sentiment="neutral",
        summary=summary,
        key_themes=titles[:3] or ["No recent news data"],
    )


async def _fetch_news_via_mcp(ticker: str) -> List[Dict[str, Any]]:
    result = await call_tool("get_stock_news", {"ticker": ticker, "limit": 10})
    return result.get("headlines") or []


async def analyze_news(ticker: str, headlines: List[Dict[str, Any]] = None) -> NewsAgentOutput:
    try:
        mcp_error = ""
        news_headlines: List[Dict[str, Any]] = headlines or []

        try:
            news_headlines = await _fetch_news_via_mcp(ticker)
        except MCPClientError as exc:
            mcp_error = str(exc)
            log_event(
                logger, logging.WARNING, "MCP news fetch failed", ticker=ticker, error=str(exc)
            )
            if not news_headlines:
                fb = _fallback([], mcp_error)
                return NewsAgentOutput(
                    **fb.model_dump(),
                    confidence_score=0.0,
                    error_message=str(exc),
                )

        fallback = _fallback(news_headlines, mcp_error)
        payload = json.dumps({"ticker": ticker, "headlines": news_headlines}, default=str)

        result = await call_openai_json(
            system_prompt=(
                "You are a financial news analyst. Analyze recent headlines for the given stock ticker. "
                "Respond ONLY with valid JSON matching this schema: "
                '{"sentiment": "bullish"|"bearish"|"neutral", "summary": string, '
                '"key_themes": [string, ...]}'
            ),
            user_prompt=f"Analyze news for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )

        try:
            return NewsAgentOutput.model_validate(result)
        except Exception:
            return fallback
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "News agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = _fallback(headlines or [], str(exc))
        return NewsAgentOutput(
            **fb.model_dump(),
            confidence_score=0.0,
            error_message=str(exc),
        )

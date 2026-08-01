"""Macro Agent — flags portfolio-relevant macro themes from news flow."""

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Dict, List

from app.agents.llm import call_openai_json
from app.models.agent_schemas import MacroAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("macro_agent")

_MACRO_KEYWORDS = (
    "fed", "fomc", "cpi", "inflation", "rates", "treasury", "employment",
    "jobs", "gdp", "recession", "oil", "yield", "dollar", "tariff", "macro",
)


def _fallback(ticker: str, headlines: List[Dict[str, Any]]) -> MacroAgentOutput:
    themes = []
    for h in headlines[:10]:
        title = (h.get("title") or "").lower()
        for kw in _MACRO_KEYWORDS:
            if kw in title and kw not in themes:
                themes.append(kw)
    relevance = "high" if len(themes) >= 3 else "medium" if themes else "low"
    return MacroAgentOutput(
        macro_summary=(
            f"Macro scan for {ticker} based on recent headlines. "
            + (
                f"Detected themes: {', '.join(themes[:5])}."
                if themes
                else "No strong macro keywords in the latest headlines."
            )
        ),
        relevance=relevance if themes else "none",
        themes=themes[:6] or ["limited macro signal"],
        portfolio_implications=[
            "Re-check rate-sensitive valuation if yields move sharply.",
            "Watch CPI/FOMC calendar for sector volatility.",
        ],
        confidence_score=0.4 if themes else 0.3,
    )


async def analyze_macro(
    ticker: str,
    news_headlines: List[Dict[str, Any]],
    news_output: Dict[str, Any],
) -> MacroAgentOutput:
    fallback = _fallback(ticker, news_headlines or [])
    try:
        payload = {
            "ticker": ticker,
            "news_sentiment": news_output.get("sentiment"),
            "news_summary": news_output.get("summary"),
            "headlines": [
                {"title": h.get("title", ""), "publisher": h.get("publisher", "")}
                for h in (news_headlines or [])[:10]
            ],
        }
        result = await call_openai_json(
            system_prompt=(
                "You are a macroeconomic research analyst linking macro events "
                "(CPI, FOMC, employment, rates, growth) to a single equity. "
                "Do not invent economic prints. Respond ONLY with JSON: "
                '{"macro_summary": string, "relevance": "high"|"medium"|"low"|"none", '
                '"themes": [string,...], "portfolio_implications": [string,...], '
                '"confidence_score": number 0-1}'
            ),
            user_prompt=f"Macro relevance for {ticker}:\n{json.dumps(payload, default=str)}",
            fallback=fallback.model_dump(),
        )
        return MacroAgentOutput.model_validate(result)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Macro agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback.model_dump()
        fb["error_message"] = str(exc)
        fb["confidence_score"] = 0.0
        return MacroAgentOutput.model_validate(fb)

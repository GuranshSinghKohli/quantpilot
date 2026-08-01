"""Portfolio Agent — synthesizes a daily briefing across user holdings."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import Any, Dict, List, Optional

from app.agents.llm import call_openai_json
from app.config import BRIEFING_MAX_HOLDINGS
from app.models.agent_schemas import PortfolioAnalysis, PortfolioBriefingOutput
from app.observability.logger import get_logger, log_event
from app.services.portfolio_analyzer import analyze_portfolio
from app.services.yahoo_finance import get_ticker_news

logger = get_logger("portfolio_agent")


def _fallback(analysis: PortfolioAnalysis) -> PortfolioBriefingOutput:
    n = len(analysis.holdings)
    if n == 0:
        return PortfolioBriefingOutput(
            headline="No holdings to brief",
            summary="Add tickers to your portfolio to receive a daily briefing.",
            confidence_score=0.0,
        )
    weakest = analysis.weakest_ticker
    highlights = [analysis.summary] if analysis.summary else [f"{n} holdings monitored."]
    risks = []
    if weakest:
        risks.append(f"Keep a closer eye on {weakest}.")
    high = analysis.risk_mix.get("HIGH", 0)
    if high:
        risks.append(f"{high} holding(s) flagged HIGH risk by heuristic screen.")
    return PortfolioBriefingOutput(
        headline=f"Daily look across {n} holding{'s' if n != 1 else ''}",
        summary=analysis.summary
        or "Heuristic portfolio screen completed. Sign in with OpenAI configured for richer narrative briefings.",
        highlights=highlights,
        risks=risks,
        watch_tickers=[weakest] if weakest else [],
        confidence_score=0.45,
    )


async def _news_for_tickers(tickers: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    async def one(t: str) -> tuple:
        try:
            headlines = await get_ticker_news(t)
            return t, headlines[:3]
        except Exception:
            return t, []

    pairs = await asyncio.gather(*[one(t) for t in tickers])
    return {t: h for t, h in pairs}


async def generate_portfolio_briefing(
    tickers: Optional[List[str]] = None,
) -> tuple[PortfolioBriefingOutput, PortfolioAnalysis]:
    """
    Monitor the full holdings set: run basket analysis, pull light news,
    and synthesize a structured daily briefing.
    """
    symbols = list(
        dict.fromkeys(
            t.upper().strip()
            for t in (tickers or [])
            if t and t.strip()
        )
    )[: max(1, BRIEFING_MAX_HOLDINGS)]

    analysis = await analyze_portfolio(symbols)
    fallback = _fallback(analysis)

    if not analysis.holdings:
        return fallback, analysis

    news_by_ticker = await _news_for_tickers([h.ticker for h in analysis.holdings])

    try:
        payload = {
            "portfolio_summary": analysis.summary,
            "avg_pe": analysis.avg_pe,
            "risk_mix": analysis.risk_mix,
            "weakest_ticker": analysis.weakest_ticker,
            "holdings": [h.model_dump() for h in analysis.holdings],
            "recent_headlines": {
                t: [{"title": x.get("title", ""), "publisher": x.get("publisher", "")} for x in items]
                for t, items in news_by_ticker.items()
            },
        }
        result = await call_openai_json(
            system_prompt=(
                "You are a portfolio research analyst writing a concise daily briefing "
                "for a retail investor. Separate facts from judgment. "
                "This is research tooling, NOT investment advice. "
                "Respond ONLY with JSON: "
                '{"headline": string (<=120 chars), "summary": string (2-4 sentences), '
                '"highlights": [string,...] (3-6 items), "risks": [string,...] (1-4 items), '
                '"watch_tickers": [string,...] (tickers to watch today), '
                '"confidence_score": number 0-1}'
            ),
            user_prompt=(
                "Write today's portfolio briefing from this data:\n"
                f"{json.dumps(payload, default=str)}"
            ),
            fallback=fallback.model_dump(),
        )
        output = PortfolioBriefingOutput.model_validate(result)
        if not output.headline:
            output.headline = fallback.headline
        if not output.summary:
            output.summary = fallback.summary
        return output, analysis
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Portfolio agent failed",
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback.model_dump()
        fb["error_message"] = str(exc)
        fb["confidence_score"] = 0.0
        return PortfolioBriefingOutput.model_validate(fb), analysis

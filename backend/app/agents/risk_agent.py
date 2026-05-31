import json
import logging
import traceback
from typing import Any, Dict

from app.agents.llm import call_openai_json
from app.observability.logger import get_logger, log_event
from app.models.agent_schemas import (
    FinancialMetricsAgentOutput,
    NewsAgentOutput,
    RiskAgentOutput,
    SECFilingAgentOutput,
)

logger = get_logger("risk_agent")


def _fallback(
    news: NewsAgentOutput,
    metrics: FinancialMetricsAgentOutput,
    sec: SECFilingAgentOutput,
) -> RiskAgentOutput:
    factors = []
    level = "MEDIUM"

    if news.sentiment == "bearish":
        factors.append("Bearish news sentiment")
        level = "HIGH"
    elif news.sentiment == "bullish":
        factors.append("Bullish news sentiment")

    if metrics.valuation_rating == "overvalued":
        factors.append("Valuation appears stretched")
        level = "HIGH"
    elif metrics.valuation_rating == "undervalued":
        factors.append("Valuation may offer margin of safety")

    if sec.risk_signals:
        factors.extend(sec.risk_signals[:3])

    if not factors:
        factors.append("Limited risk signals from available data")

    return RiskAgentOutput(
        risk_level=level,
        risk_factors=factors,
        confidence_score=0.5,
    )


async def assess_risk(
    ticker: str,
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
) -> RiskAgentOutput:
    try:
        news = NewsAgentOutput.model_validate(news_output)
        metrics = FinancialMetricsAgentOutput.model_validate(metrics_output)
        sec = SECFilingAgentOutput.model_validate(sec_output)
        fallback = _fallback(news, metrics, sec)

        payload = json.dumps(
            {
                "ticker": ticker,
                "news_output": news_output,
                "metrics_output": metrics_output,
                "sec_output": sec_output,
            },
            default=str,
        )

        result = await call_openai_json(
            system_prompt=(
                "You are a risk management analyst. Synthesize inputs into an overall risk view. "
                "Respond ONLY with valid JSON: "
                '{"risk_level": "LOW"|"MEDIUM"|"HIGH", "risk_factors": [string, ...], '
                '"confidence_score": number between 0.0 and 1.0}'
            ),
            user_prompt=f"Synthesize risk for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )

        try:
            return RiskAgentOutput.model_validate(result)
        except Exception:
            return fallback
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Risk agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return RiskAgentOutput(
            risk_level="MEDIUM",
            risk_factors=[f"Risk assessment unavailable: {exc}"],
            confidence_score=0.0,
            error_message=str(exc),
        )

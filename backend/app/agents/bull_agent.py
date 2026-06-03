import json
import logging
import traceback
from typing import Any, Dict

from app.agents.llm import call_openai_json
from app.models.agent_schemas import DebateAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("bull_agent")


def _fallback(metrics: Dict[str, Any], news: Dict[str, Any]) -> DebateAgentOutput:
    rating = metrics.get("valuation_rating", "fairly valued")
    sentiment = news.get("sentiment", "neutral")
    return DebateAgentOutput(
        stance="bull",
        thesis=f"Constructive case for growth and market position despite {rating} valuation.",
        key_points=[
            f"News sentiment: {sentiment}",
            "Strong franchise and cash generation potential",
            "Long-term secular tailwinds in sector",
        ],
        confidence_score=0.55,
    )


async def argue_bull(
    ticker: str,
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    risk_output: Dict[str, Any],
) -> DebateAgentOutput:
    fallback = _fallback(metrics_output, news_output)
    try:
        payload = json.dumps(
            {
                "ticker": ticker,
                "news_output": news_output,
                "metrics_output": metrics_output,
                "sec_output": sec_output,
                "risk_output": risk_output,
            },
            default=str,
        )
        result = await call_openai_json(
            system_prompt=(
                "You are a bullish equity analyst making the strongest honest bull case. "
                "Respond ONLY with JSON: "
                '{"stance": "bull", "thesis": string, "key_points": [string,...], '
                '"confidence_score": number 0-1}'
            ),
            user_prompt=f"Build the bull case for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )
        result["stance"] = "bull"
        return DebateAgentOutput.model_validate(result)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Bull agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback
        return DebateAgentOutput(
            **fb.model_dump(),
            confidence_score=0.0,
            error_message=str(exc),
        )

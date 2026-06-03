import json
import logging
import traceback
from typing import Any, Dict

from app.agents.llm import call_openai_json
from app.models.agent_schemas import DebateAgentOutput
from app.observability.logger import get_logger, log_event

logger = get_logger("bear_agent")


def _fallback(metrics: Dict[str, Any], risk: Dict[str, Any]) -> DebateAgentOutput:
    return DebateAgentOutput(
        stance="bear",
        thesis="Caution warranted given valuation, macro uncertainty, and identifiable risk factors.",
        key_points=[
            f"Risk level flagged: {risk.get('risk_level', 'MEDIUM')}",
            f"Valuation view: {metrics.get('valuation_rating', 'fairly valued')}",
            "Potential for near-term drawdown if sentiment shifts",
        ],
        confidence_score=0.55,
    )


async def argue_bear(
    ticker: str,
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    risk_output: Dict[str, Any],
) -> DebateAgentOutput:
    fallback = _fallback(metrics_output, risk_output)
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
                "You are a bearish equity analyst making the strongest honest bear case. "
                "Respond ONLY with JSON: "
                '{"stance": "bear", "thesis": string, "key_points": [string,...], '
                '"confidence_score": number 0-1}'
            ),
            user_prompt=f"Build the bear case for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )
        result["stance"] = "bear"
        return DebateAgentOutput.model_validate(result)
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Bear agent failed",
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

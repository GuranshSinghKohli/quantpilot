import json
import logging
import traceback
from typing import Any, Dict, Optional

from app.agents.llm import call_openai_json
from app.models.agent_schemas import FinalReportOutput, ReportSection
from app.observability.logger import get_logger, log_event

logger = get_logger("report_agent")


def _fallback(
    ticker: str,
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    risk_output: Dict[str, Any],
) -> FinalReportOutput:
    return FinalReportOutput(
        ticker=ticker,
        report_title=f"{ticker} — QuantPilot Research Snapshot",
        executive_summary=(
            f"Automated multi-agent snapshot for {ticker}. "
            f"News sentiment: {news_output.get('sentiment', 'neutral')}. "
            f"Valuation: {metrics_output.get('valuation_rating', 'fairly valued')}. "
            f"Risk level: {risk_output.get('risk_level', 'MEDIUM')}."
        ),
        sections=[
            ReportSection(
                title="News & Sentiment",
                content=news_output.get("summary", ""),
            ),
            ReportSection(
                title="Financial Metrics",
                content=metrics_output.get("analysis_summary", ""),
            ),
            ReportSection(
                title="SEC Filings",
                content=sec_output.get("filing_summary", ""),
            ),
            ReportSection(
                title="Risk Assessment",
                content=", ".join(risk_output.get("risk_factors", [])),
            ),
        ],
        recommendation="Hold — conduct further due diligence before investing.",
    )


async def generate_report(
    ticker: str,
    stock_data: Dict[str, Any],
    news_output: Dict[str, Any],
    metrics_output: Dict[str, Any],
    sec_output: Dict[str, Any],
    risk_output: Dict[str, Any],
    debate_output: Optional[Dict[str, Any]] = None,
) -> FinalReportOutput:
    fallback = _fallback(
        ticker, news_output, metrics_output, sec_output, risk_output
    )
    try:
        payload = json.dumps(
            {
                "ticker": ticker,
                "stock_data": stock_data,
                "news_output": news_output,
                "metrics_output": metrics_output,
                "sec_output": sec_output,
                "risk_output": risk_output,
                "debate_output": debate_output or {},
            },
            default=str,
        )

        result = await call_openai_json(
            system_prompt=(
                "You are a senior equity research analyst. Write a structured research report. "
                "Synthesize bull and bear debate when provided. "
                "Respond ONLY with valid JSON: "
                '{"ticker": string, "report_title": string, "executive_summary": string, '
                '"sections": [{"title": string, "content": string}, ...], '
                '"recommendation": string, "disclaimer": string}'
            ),
            user_prompt=f"Generate research report for {ticker}:\n{payload}",
            fallback=fallback.model_dump(),
        )

        try:
            report = FinalReportOutput.model_validate(result)
            report.ticker = ticker
            return report
        except Exception:
            return fallback
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Report agent failed",
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        fb = fallback
        return FinalReportOutput(
            **fb.model_dump(),
            confidence_score=0.0,
            error_message=str(exc),
        )
"""Build AnalysisResponse from LangGraph state — shared by sync and stream routes."""

from typing import Any, Dict

from app.evaluation.confidence import overall_confidence
from app.evaluation.fact_separator import separate_facts_and_insights
from app.models.agent_schemas import (
    AnalysisResponse,
    DebateAgentOutput,
    DebateOutput,
    FactsAndInsights,
    FinalReportOutput,
    FinancialMetricsAgentOutput,
    NewsAgentOutput,
    PerAgentConfidence,
    RiskAgentOutput,
    SECFilingAgentOutput,
)


def build_analysis_response(state: Dict[str, Any], run_id: str) -> AnalysisResponse:
    symbol = state["ticker"]
    final_report_raw = state.get("final_report") or {}

    scores = state.get("confidence_scores") or {}
    per_agent = PerAgentConfidence(
        news=scores.get("news", 0.0),
        financial=scores.get("financial", 0.0),
        sec=scores.get("sec", 0.0),
        risk=scores.get("risk", 0.0),
        bull=scores.get("bull", 0.0),
        bear=scores.get("bear", 0.0),
        report=scores.get("report", 0.0),
    )
    overall = overall_confidence(per_agent.model_dump())

    facts_raw = state.get("facts_and_insights") or separate_facts_and_insights(
        final_report_raw,
        state.get("metrics_output") or {},
        state.get("sec_output") or {},
        state.get("filings_data"),
        state.get("news_headlines"),
    )

    debate_raw = state.get("debate_output")
    debate_output = None
    if debate_raw:
        debate_output = DebateOutput(
            bull=DebateAgentOutput.model_validate(debate_raw.get("bull", {})),
            bear=DebateAgentOutput.model_validate(debate_raw.get("bear", {})),
        )

    return AnalysisResponse(
        ticker=symbol,
        final_report=FinalReportOutput.model_validate(final_report_raw),
        news_output=(
            NewsAgentOutput.model_validate(state["news_output"])
            if state.get("news_output")
            else None
        ),
        metrics_output=(
            FinancialMetricsAgentOutput.model_validate(state["metrics_output"])
            if state.get("metrics_output")
            else None
        ),
        sec_output=(
            SECFilingAgentOutput.model_validate(state["sec_output"])
            if state.get("sec_output")
            else None
        ),
        risk_output=(
            RiskAgentOutput.model_validate(state["risk_output"])
            if state.get("risk_output")
            else None
        ),
        run_id=run_id,
        overall_confidence_score=overall,
        per_agent_confidence=per_agent,
        validation_warnings=state.get("validation_warnings") or [],
        facts_and_insights=FactsAndInsights.model_validate(facts_raw),
        debate_output=debate_output,
    )

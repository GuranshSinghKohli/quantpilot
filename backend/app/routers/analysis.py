import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.evaluation.confidence import overall_confidence
from app.evaluation.fact_separator import separate_facts_and_insights
from app.memory import report_memory
from app.models.agent_schemas import (
    AnalysisResponse,
    FactsAndInsights,
    FinalReportOutput,
    FinancialMetricsAgentOutput,
    NewsAgentOutput,
    PerAgentConfidence,
    RiskAgentOutput,
    SECFilingAgentOutput,
)
from app.observability.logger import get_logger, log_event
from app.observability.workflow_tracker import workflow_tracker
from app.workflows.analysis_graph import run_analysis

router = APIRouter(tags=["analysis"])
logger = get_logger("analysis_router")


@router.post("/analysis/{ticker}", response_model=AnalysisResponse)
async def analyze_ticker(
    ticker: str, background_tasks: BackgroundTasks
) -> AnalysisResponse:
    symbol = ticker.upper().strip()
    if not symbol or len(symbol) > 10:
        raise HTTPException(
            status_code=400,
            detail="Invalid ticker symbol.",
        )

    run = workflow_tracker.start_run(symbol)
    start = time.perf_counter()

    try:
        state = await run_analysis(symbol, run_id=run["run_id"])
    except Exception as exc:
        workflow_tracker.complete_run(run["run_id"], status="failed")
        log_event(
            logger,
            logging.ERROR,
            "Analysis workflow failed",
            route=f"/api/analysis/{symbol}",
            ticker=symbol,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Analysis workflow failed: {exc}",
        ) from exc

    duration_ms = int((time.perf_counter() - start) * 1000)

    if state.get("error"):
        workflow_tracker.complete_run(run["run_id"], status="failed")
        log_event(
            logger,
            logging.WARNING,
            "Analysis route completed with pipeline error",
            route=f"/api/analysis/{symbol}",
            ticker=symbol,
            status_code=502,
            duration_ms=duration_ms,
        )
        raise HTTPException(status_code=502, detail=state["error"])

    final_report_raw = state.get("final_report")
    if not final_report_raw:
        workflow_tracker.complete_run(run["run_id"], status="failed")
        raise HTTPException(
            status_code=502,
            detail="Analysis completed without a final report.",
        )

    scores = state.get("confidence_scores") or {}
    per_agent = PerAgentConfidence(
        news=scores.get("news", 0.0),
        financial=scores.get("financial", 0.0),
        sec=scores.get("sec", 0.0),
        risk=scores.get("risk", 0.0),
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

    workflow_tracker.complete_run(
        run["run_id"],
        status="completed",
        overall_confidence=overall,
    )

    log_event(
        logger,
        logging.INFO,
        "Analysis route completed",
        route=f"/api/analysis/{symbol}",
        ticker=symbol,
        status_code=200,
        duration_ms=duration_ms,
        run_id=run["run_id"],
        overall_confidence=overall,
    )

    response = AnalysisResponse(
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
        run_id=run["run_id"],
        overall_confidence_score=overall,
        per_agent_confidence=per_agent,
        validation_warnings=state.get("validation_warnings") or [],
        facts_and_insights=FactsAndInsights.model_validate(facts_raw),
    )

    background_tasks.add_task(report_memory.persist_analysis, state)
    return response

import logging
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.financial_metrics_agent import analyze_financial_metrics
from app.agents.news_agent import analyze_news
from app.agents.report_agent import generate_report
from app.agents.risk_agent import assess_risk
from app.agents.sec_filing_agent import analyze_sec_filings
from app.evaluation.confidence import (
    overall_confidence,
    score_financial_agent,
    score_news_agent,
    score_report_agent,
    score_risk_agent,
    score_sec_agent,
)
from app.evaluation.fact_separator import separate_facts_and_insights
from app.evaluation.validator import (
    validate_financial,
    validate_news,
    validate_report,
    validate_risk,
    validate_sec,
)
from app.observability.logger import get_logger, log_event
from app.observability.workflow_tracker import workflow_tracker
from app.services.sec_edgar import get_recent_filings
from app.services.yahoo_finance import get_stock_data, get_ticker_news

logger = get_logger("analysis_graph")


class AnalysisState(TypedDict, total=False):
    ticker: str
    run_id: str
    stock_data: Dict[str, Any]
    filings_data: Dict[str, Any]
    news_headlines: List[Dict[str, Any]]
    news_output: Dict[str, Any]
    metrics_output: Dict[str, Any]
    sec_output: Dict[str, Any]
    risk_output: Dict[str, Any]
    final_report: Dict[str, Any]
    confidence_scores: Dict[str, float]
    validation_warnings: List[str]
    facts_and_insights: Dict[str, Any]
    error: str


def _append_warnings(state: AnalysisState, warnings: List[str]) -> List[str]:
    existing = list(state.get("validation_warnings") or [])
    existing.extend(warnings)
    return existing


async def _execute_agent_step(
    state: AnalysisState,
    agent_name: str,
    run_fn: Callable,
    validate_fn: Callable[[Dict[str, Any]], Tuple[Dict[str, Any], List[str]]],
    score_fn: Callable,
    output_key: str,
    **score_kwargs: Any,
) -> AnalysisState:
    if state.get("error"):
        return state

    ticker = state["ticker"]
    run_id = state.get("run_id", "")
    log_event(logger, logging.INFO, "Agent started", agent=agent_name, ticker=ticker)

    start = time.perf_counter()
    raw: Dict[str, Any] = {}
    step_status = "success"
    step_error: Optional[str] = None

    try:
        result = await run_fn()
        raw = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as exc:
        step_status = "failed"
        step_error = str(exc)
        log_event(
            logger,
            logging.ERROR,
            "Agent failed",
            agent=agent_name,
            ticker=ticker,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        raw = {
            "error_message": str(exc),
            "confidence_score": 0.0,
        }

    confidence = score_fn(raw, **score_kwargs) if step_status == "success" else 0.0
    raw["confidence_score"] = confidence

    validated, warnings = validate_fn(raw)
    if warnings:
        validated["validation_warning"] = "; ".join(warnings)

    duration_ms = int((time.perf_counter() - start) * 1000)
    log_event(
        logger,
        logging.INFO,
        "Agent completed",
        agent=agent_name,
        ticker=ticker,
        confidence_score=validated.get("confidence_score", confidence),
        duration_ms=duration_ms,
    )

    if run_id:
        workflow_tracker.record_step(
            run_id,
            agent_name,
            step_status,
            duration_ms,
            confidence_score=validated.get("confidence_score"),
            error=step_error,
        )

    agent_key_map = {
        "news_agent": "news",
        "financial_agent": "financial",
        "sec_agent": "sec",
        "risk_agent": "risk",
        "report_agent": "report",
    }
    scores = dict(state.get("confidence_scores") or {})
    scores[agent_key_map.get(agent_name, agent_name)] = validated.get(
        "confidence_score", confidence
    )

    return {
        **state,
        output_key: validated,
        "confidence_scores": scores,
        "validation_warnings": _append_warnings(state, warnings),
    }


async def fetch_data(state: AnalysisState) -> AnalysisState:
    ticker = state["ticker"]
    try:
        stock = await get_stock_data(ticker)
        filings = await get_recent_filings(ticker, limit=3)
        headlines = await get_ticker_news(ticker)
        return {
            **state,
            "stock_data": stock.model_dump(),
            "filings_data": filings.model_dump(),
            "news_headlines": headlines,
        }
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "Data fetch failed",
            agent="fetch_data",
            ticker=ticker,
            error=str(exc),
        )
        return {**state, "error": f"Data fetch failed: {exc}"}


async def news_analysis(state: AnalysisState) -> AnalysisState:
    return await _execute_agent_step(
        state,
        "news_agent",
        lambda: analyze_news(state["ticker"], state.get("news_headlines") or []),
        validate_news,
        score_news_agent,
        "news_output",
        headlines=state.get("news_headlines") or [],
    )


async def financial_analysis(state: AnalysisState) -> AnalysisState:
    return await _execute_agent_step(
        state,
        "financial_agent",
        lambda: analyze_financial_metrics(
            state["ticker"], state.get("stock_data") or {}
        ),
        validate_financial,
        score_financial_agent,
        "metrics_output",
    )


async def sec_analysis(state: AnalysisState) -> AnalysisState:
    return await _execute_agent_step(
        state,
        "sec_agent",
        lambda: analyze_sec_filings(
            state["ticker"], state.get("filings_data") or {}
        ),
        validate_sec,
        score_sec_agent,
        "sec_output",
        filings_data=state.get("filings_data"),
    )


async def risk_assessment(state: AnalysisState) -> AnalysisState:
    if state.get("error"):
        return state

    scores = state.get("confidence_scores") or {}
    news_conf = scores.get("news", 0.5)
    fin_conf = scores.get("financial", 0.5)
    sec_conf = scores.get("sec", 0.5)

    async def run_risk():
        return await assess_risk(
            state["ticker"],
            state.get("news_output") or {},
            state.get("metrics_output") or {},
            state.get("sec_output") or {},
        )

    updated = await _execute_agent_step(
        state,
        "risk_agent",
        run_risk,
        validate_risk,
        lambda raw, **_: score_risk_agent(news_conf, fin_conf, sec_conf),
        "risk_output",
    )
    risk_out = updated.get("risk_output") or {}
    risk_conf = score_risk_agent(news_conf, fin_conf, sec_conf)
    risk_out["confidence_score"] = risk_conf
    scores = dict(updated.get("confidence_scores") or {})
    scores["risk"] = risk_conf
    return {**updated, "risk_output": risk_out, "confidence_scores": scores}


async def final_report(state: AnalysisState) -> AnalysisState:
    if state.get("error"):
        return state

    scores = state.get("confidence_scores") or {}
    risk_conf = scores.get("risk", 0.5)
    ticker = state["ticker"]

    async def run_report():
        return await generate_report(
            ticker,
            state.get("stock_data") or {},
            state.get("news_output") or {},
            state.get("metrics_output") or {},
            state.get("sec_output") or {},
            state.get("risk_output") or {},
        )

    updated = await _execute_agent_step(
        state,
        "report_agent",
        run_report,
        lambda raw: validate_report(raw, ticker),
        lambda raw, **_: score_report_agent(raw, risk_conf),
        "final_report",
    )

    validated_report = updated.get("final_report") or {}
    report_conf = validated_report.get("confidence_score", risk_conf)
    facts = separate_facts_and_insights(
        validated_report,
        state.get("metrics_output") or {},
        state.get("sec_output") or {},
        state.get("filings_data"),
        state.get("news_headlines"),
    )

    scores = dict(updated.get("confidence_scores") or {})
    scores["report"] = report_conf

    return {
        **updated,
        "final_report": validated_report,
        "confidence_scores": scores,
        "facts_and_insights": facts,
    }


def _build_graph():
    workflow = StateGraph(AnalysisState)

    workflow.add_node("fetch_data", fetch_data)
    workflow.add_node("news_analysis", news_analysis)
    workflow.add_node("financial_analysis", financial_analysis)
    workflow.add_node("sec_analysis", sec_analysis)
    workflow.add_node("risk_assessment", risk_assessment)
    workflow.add_node("final_report", final_report)

    workflow.set_entry_point("fetch_data")
    workflow.add_edge("fetch_data", "news_analysis")
    workflow.add_edge("news_analysis", "financial_analysis")
    workflow.add_edge("financial_analysis", "sec_analysis")
    workflow.add_edge("sec_analysis", "risk_assessment")
    workflow.add_edge("risk_assessment", "final_report")
    workflow.add_edge("final_report", END)

    return workflow.compile()


_graph = None


def get_analysis_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


async def run_analysis(ticker: str, run_id: Optional[str] = None) -> AnalysisState:
    graph = get_analysis_graph()
    initial: AnalysisState = {
        "ticker": ticker.upper().strip(),
        "run_id": run_id or "",
        "validation_warnings": [],
        "confidence_scores": {},
    }
    return await graph.ainvoke(initial)

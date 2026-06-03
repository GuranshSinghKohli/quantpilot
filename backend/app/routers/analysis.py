import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from app.memory import report_memory
from app.models.agent_schemas import AnalysisResponse, ChatRequest, ChatResponse, PortfolioAnalysis
from app.observability.logger import get_logger, log_event
from app.observability.workflow_tracker import workflow_tracker
from app.services.portfolio_analyzer import analyze_portfolio
from app.services.research_chat import answer_research_question
from app.workflows.analysis_graph import run_analysis, stream_analysis
from app.workflows.analysis_response import build_analysis_response

router = APIRouter(tags=["analysis"])
logger = get_logger("analysis_router")


def _validate_ticker(ticker: str) -> str:
    symbol = ticker.upper().strip()
    if not symbol or len(symbol) > 10:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol.")
    return symbol


@router.post("/analysis/{ticker}", response_model=AnalysisResponse)
async def analyze_ticker(
    ticker: str, background_tasks: BackgroundTasks
) -> AnalysisResponse:
    symbol = _validate_ticker(ticker)
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
        raise HTTPException(status_code=502, detail=state["error"])

    if not state.get("final_report"):
        workflow_tracker.complete_run(run["run_id"], status="failed")
        raise HTTPException(
            status_code=502,
            detail="Analysis completed without a final report.",
        )

    response = build_analysis_response(state, run["run_id"])
    workflow_tracker.complete_run(
        run["run_id"],
        status="completed",
        overall_confidence=response.overall_confidence_score,
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
        overall_confidence=response.overall_confidence_score,
    )

    background_tasks.add_task(report_memory.persist_analysis, state)
    return response


@router.post("/analysis/{ticker}/stream")
async def analyze_ticker_stream(ticker: str) -> StreamingResponse:
    symbol = _validate_ticker(ticker)
    run = workflow_tracker.start_run(symbol)

    async def event_generator():
        start = time.perf_counter()
        final_state = None
        try:
            async for event in stream_analysis(symbol, run_id=run["run_id"]):
                if event.get("type") == "pipeline_completed":
                    final_state = event.get("state")
                    response = build_analysis_response(final_state, run["run_id"])
                    workflow_tracker.complete_run(
                        run["run_id"],
                        status="completed",
                        overall_confidence=response.overall_confidence_score,
                    )
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    log_event(
                        logger,
                        logging.INFO,
                        "Analysis stream completed",
                        route=f"/api/analysis/{symbol}/stream",
                        ticker=symbol,
                        duration_ms=duration_ms,
                        run_id=run["run_id"],
                    )
                    payload = {
                        "type": "result",
                        "data": response.model_dump(mode="json"),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                    # Persist after stream completes — avoids overlapping with next request
                    try:
                        await report_memory.persist_analysis(final_state)
                    except Exception:
                        pass
                elif event.get("type") == "error":
                    workflow_tracker.complete_run(run["run_id"], status="failed")
                    payload = {"type": "error", "message": event.get("message", "")}
                    yield f"data: {json.dumps(payload)}\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            workflow_tracker.complete_run(run["run_id"], status="failed")
            log_event(
                logger,
                logging.ERROR,
                "Analysis stream failed",
                ticker=symbol,
                error=str(exc),
            )
            payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/{ticker}", response_model=ChatResponse)
async def research_chat(ticker: str, body: ChatRequest) -> ChatResponse:
    symbol = _validate_ticker(ticker)
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="Question too long.")

    return await answer_research_question(
        symbol, question, body.analysis_context
    )


@router.get("/portfolio/analyze", response_model=PortfolioAnalysis)
async def portfolio_analyze() -> PortfolioAnalysis:
    return await analyze_portfolio()

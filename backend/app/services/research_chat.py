import json
import logging
from typing import Any, Dict, List, Optional

from app.evaluation.confidence import score_debate_agent
from app.evaluation.validator import validate_report
from app.memory import chroma_store
from app.models.agent_schemas import ChatResponse
from app.agents.llm import call_openai_text
from app.observability.logger import get_logger, log_event

logger = get_logger("research_chat")


def _format_context(
    ticker: str,
    analysis_context: Optional[Dict[str, Any]],
    similar: List[Dict[str, Any]],
) -> str:
    parts: List[str] = [f"Ticker: {ticker}"]

    if analysis_context:
        report = analysis_context.get("final_report") or analysis_context
        if isinstance(report, dict):
            summary = report.get("executive_summary") or report.get("summary", "")
            rec = report.get("recommendation", "")
            if summary:
                parts.append(f"Current report summary: {summary}")
            if rec:
                parts.append(f"Recommendation: {rec}")

        debate = analysis_context.get("debate_output")
        if debate:
            parts.append(f"Bull vs bear debate: {json.dumps(debate, default=str)[:1500]}")

        risk = analysis_context.get("risk_output")
        if risk:
            parts.append(f"Risk: {json.dumps(risk, default=str)[:500]}")

    for item in similar[:3]:
        meta = item.get("metadata") or {}
        report = item.get("report") or {}
        snippet = ""
        if isinstance(report, dict):
            snippet = report.get("executive_summary") or report.get("raw", "")[:400]
        parts.append(
            f"Past analysis ({meta.get('ticker', '?')}): {snippet}"
        )

    return "\n\n".join(parts)


async def answer_research_question(
    ticker: str,
    question: str,
    analysis_context: Optional[Dict[str, Any]] = None,
) -> ChatResponse:
    symbol = ticker.upper().strip()
    sources_used: List[str] = ["current_analysis"]

    similar: List[Dict[str, Any]] = []
    try:
        similar = chroma_store.search_similar(
            f"{symbol} {question}", n_results=3
        )
        if similar:
            sources_used.append("vector_memory")
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Chat memory search skipped",
            ticker=symbol,
            error=str(exc),
        )

    context_block = _format_context(symbol, analysis_context, similar)
    fallback = (
        f"I don't have enough context to answer precisely about {symbol}. "
        "Run a full analysis first, then ask follow-up questions."
    )

    answer = await call_openai_text(
        system_prompt=(
            "You are QuantPilot, an equity research copilot. Answer using ONLY the "
            "provided context. Cite uncertainty when data is missing. "
            "Keep answers concise (2-4 paragraphs max). Not financial advice."
        ),
        user_prompt=f"Context:\n{context_block}\n\nQuestion: {question}",
        fallback=fallback,
    )

    return ChatResponse(ticker=symbol, answer=answer, sources_used=sources_used)

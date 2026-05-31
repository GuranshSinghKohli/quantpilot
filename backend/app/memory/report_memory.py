import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.memory import chroma_store, session_store

logger = logging.getLogger(__name__)


def _extract_metadata(state: Dict[str, Any]) -> Dict[str, str]:
    final_report = state.get("final_report") or {}
    risk_output = state.get("risk_output") or {}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recommendation": str(final_report.get("recommendation", "")),
        "risk_level": str(risk_output.get("risk_level", "")),
    }


def save_report_from_state(state: Dict[str, Any]) -> None:
    """Persist analysis to ChromaDB and session history (sync, for background thread)."""
    if state.get("error") or not state.get("final_report"):
        return

    ticker = state["ticker"]
    metadata = _extract_metadata(state)
    final_report = state["final_report"]

    try:
        chroma_store.save_report(ticker, final_report, metadata)
    except Exception as exc:
        logger.warning("ChromaDB save failed for %s: %s", ticker, exc)

    try:
        session_store.add_session_entry(
            ticker=ticker,
            recommendation=metadata["recommendation"],
            risk_level=metadata["risk_level"],
        )
    except Exception as exc:
        logger.warning("Session store update failed for %s: %s", ticker, exc)


async def persist_analysis(state: Dict[str, Any]) -> None:
    await asyncio.to_thread(save_report_from_state, state)


def schedule_persist(state: Dict[str, Any]) -> None:
    """Fire-and-forget persist (used from LangGraph node)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(persist_analysis(state))
    except RuntimeError:
        save_report_from_state(state)

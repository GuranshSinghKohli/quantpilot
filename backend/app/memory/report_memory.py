import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.db.models import User
from app.db.session import SessionLocal
from app.memory import chroma_store
from app.services import analysis_history_store

logger = logging.getLogger(__name__)

LIGHTWEIGHT = os.getenv("QUANTPILOT_LIGHTWEIGHT", "").lower() in ("1", "true", "yes")


def _extract_metadata(state: Dict[str, Any]) -> Dict[str, str]:
    final_report = state.get("final_report") or {}
    risk_output = state.get("risk_output") or {}
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recommendation": str(final_report.get("recommendation", "")),
        "risk_level": str(risk_output.get("risk_level", "")),
    }


def save_report_from_state(
    state: Dict[str, Any],
    *,
    owner_key: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """Persist analysis to ChromaDB and per-owner SQL history."""
    if state.get("error") or not state.get("final_report"):
        return

    ticker = state["ticker"]
    metadata = _extract_metadata(state)
    final_report = state["final_report"]
    chroma_doc_id: Optional[str] = None

    try:
        if not LIGHTWEIGHT:
            chroma_meta = dict(metadata)
            if owner_key:
                chroma_meta["owner_key"] = owner_key
            chroma_doc_id = chroma_store.save_report(ticker, final_report, chroma_meta)
    except Exception as exc:
        logger.warning("ChromaDB save failed for %s: %s", ticker, exc)

    if not owner_key:
        logger.warning("Skipping history persist for %s — no owner_key", ticker)
        return

    db = SessionLocal()
    try:
        user = db.get(User, user_id) if user_id is not None else None
        analysis_history_store.add_run(
            db,
            owner_key=owner_key,
            user=user,
            ticker=ticker,
            recommendation=metadata["recommendation"],
            risk_level=metadata["risk_level"],
            chroma_doc_id=chroma_doc_id,
        )
    except Exception as exc:
        logger.warning("History store update failed for %s: %s", ticker, exc)
    finally:
        db.close()


async def persist_analysis(
    state: Dict[str, Any],
    *,
    owner_key: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    await asyncio.to_thread(
        save_report_from_state,
        state,
        owner_key=owner_key,
        user_id=user_id,
    )


def schedule_persist(
    state: Dict[str, Any],
    *,
    owner_key: Optional[str] = None,
    user_id: Optional[int] = None,
) -> None:
    """Fire-and-forget persist (used from LangGraph node)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            persist_analysis(state, owner_key=owner_key, user_id=user_id)
        )
    except RuntimeError:
        save_report_from_state(state, owner_key=owner_key, user_id=user_id)

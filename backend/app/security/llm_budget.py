"""Per-investigation LLM call / token budget. Does not raise the existing defaults."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Optional

from app.config import (
    MAX_LLM_CALLS_PER_INVESTIGATION,
    MAX_RETRIES,
    MAX_TOKENS_PER_INVESTIGATION,
)

_budget: ContextVar[Optional[Dict[str, Any]]] = ContextVar("llm_budget", default=None)


def bind_investigation_budget(investigation_id: Optional[int] = None) -> Dict[str, Any]:
    state = {
        "investigation_id": investigation_id,
        "calls": 0,
        "tokens": 0,
        "max_calls": MAX_LLM_CALLS_PER_INVESTIGATION,
        "max_tokens": MAX_TOKENS_PER_INVESTIGATION,
        "max_retries": MAX_RETRIES,
        "blocked": False,
    }
    _budget.set(state)
    return state


def current_budget() -> Optional[Dict[str, Any]]:
    return _budget.get()


def allow_llm_call(estimated_tokens: int = 0) -> bool:
    state = _budget.get()
    if state is None:
        return True
    if state["calls"] >= state["max_calls"]:
        state["blocked"] = True
        return False
    if state["tokens"] + estimated_tokens > state["max_tokens"]:
        state["blocked"] = True
        return False
    return True


def record_llm_call(*, tokens: int = 0, model: str = "", latency_ms: int = 0) -> None:
    state = _budget.get()
    if state is None:
        return
    state["calls"] += 1
    state["tokens"] += max(0, int(tokens))
    state["last_model"] = model
    state["last_latency_ms"] = latency_ms


def budget_snapshot() -> Dict[str, Any]:
    return dict(_budget.get() or {})

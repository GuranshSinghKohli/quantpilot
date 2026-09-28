"""Contextvars so LangGraph state stays serializable (no Session, no credentials)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from sqlalchemy.orm import Session

from app.research.llm import LLMProvider

research_db: ContextVar[Optional[Session]] = ContextVar("research_db", default=None)
research_llm: ContextVar[Optional[LLMProvider]] = ContextVar("research_llm", default=None)
research_embed: ContextVar[object] = ContextVar("research_embed", default=None)

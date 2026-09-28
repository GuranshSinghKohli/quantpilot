"""Research execution service. Independent of FastAPI so a worker can call it later."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.observability.logger import get_logger, log_event
from app.research.graph import invoke_research
from app.research.llm import LLMError, LLMProvider, get_llm_provider
from app.research.metrics import RESEARCH_METRICS
from app.research.persist import persist_result
from app.research.runtime import research_db, research_embed, research_llm
from app.research.schemas import ResearchResult
from app.retrieval.package import RequestOversized, ResearchRequest

logger = get_logger("research")


class ResearchUnavailable(RuntimeError):
    """LLM provider cannot run. The rest of the API stays healthy."""

    def __init__(self, message: str, *, error_type: str = "llm_unavailable"):
        super().__init__(message)
        self.error_type = error_type


def run_research(
    db: Session,
    request: ResearchRequest,
    *,
    llm: Optional[LLMProvider] = None,
    embed_provider=None,
    persist: bool = True,
) -> ResearchResult:
    tokens = []
    tokens.append((research_db, research_db.set(db)))
    tokens.append((research_embed, research_embed.set(embed_provider)))
    try:
        provider = llm
        if provider is None:
            try:
                provider = get_llm_provider()
            except LLMError as exc:
                RESEARCH_METRICS.inc(
                    "research_failures_total",
                    1,
                    operation="research",
                    status="failed",
                    error_type=exc.error_type,
                    component="research",
                )
                raise ResearchUnavailable(str(exc), error_type=exc.error_type) from exc
        tokens.append((research_llm, research_llm.set(provider)))
        RESEARCH_METRICS.inc(
            "research_runs_total",
            1,
            operation="research",
            status="started",
            component="research",
        )
        raw: Dict[str, Any] = invoke_research(request.model_dump(mode="json"))
        result = ResearchResult.model_validate(raw.get("result") or {})
        if any(item.get("error_type") == "oversized" for item in result.errors):
            detail = next((item.get("detail") for item in result.errors if item.get("error_type") == "oversized"), "oversized")
            raise RequestOversized(detail or "oversized")
        if persist:
            persist_result(db, request, result)
        status = "failed" if result.errors and not result.findings and not result.verified_claims else "ok"
        if result.unavailable_components:
            status = "partial" if status == "ok" else status
        RESEARCH_METRICS.inc(
            "research_runs_total",
            1,
            operation="research",
            status=status,
            component="research",
        )
        if status == "failed":
            RESEARCH_METRICS.inc(
                "research_failures_total",
                1,
                operation="research",
                status="failed",
                component="research",
            )
        log_event(
            logger,
            20,
            "RESEARCH_RUN_FINISHED",
            extra_data={"component": "research", "operation": "research", "status": status},
        )
        return result
    except RequestOversized:
        RESEARCH_METRICS.inc(
            "research_failures_total",
            1,
            operation="research",
            status="failed",
            error_type="oversized",
            component="research",
        )
        raise
    finally:
        for var, token in reversed(tokens):
            var.reset(token)

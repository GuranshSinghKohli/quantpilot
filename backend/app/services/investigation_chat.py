"""RAG chat over an Evidence Ledger case (+ related past cases)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.agents.llm import call_openai_text
from app.models.agent_schemas import ChatResponse
from app.models.investigation_schemas import InvestigationDetail
from app.observability.logger import get_logger, log_event
from app.services import evidence_ledger_store, investigation_search

logger = get_logger("investigation_chat")


def _case_dossier(detail: InvestigationDetail) -> str:
    lines = [
        f"Ticker: {detail.ticker}",
        f"Window: {detail.window_label or 'n/a'}",
        f"Move %: {detail.move_pct}",
        f"Status: {detail.status}",
        f"Summary: {detail.summary or ''}",
    ]
    if detail.verification_notes:
        lines.append(f"Verification: {detail.verification_notes}")
    if detail.da_outcome:
        lines.append(
            f"Devil's Advocate: {detail.da_outcome.outcome} — "
            f"{detail.da_outcome.counterargument or ''}"
        )
    lines.append("Claims:")
    for claim in sorted(detail.claims or [], key=lambda c: c.rank or 999)[:8]:
        lines.append(
            f"- #{claim.rank} ({claim.confidence_score:.0%}) {claim.stance}: "
            f"{claim.statement}"
        )
    lines.append("Evidence:")
    for ev in (detail.evidence_items or [])[:10]:
        url = f" | {ev.source_url}" if ev.source_url else ""
        lines.append(
            f"- [{ev.source_type}] {ev.title}: {(ev.excerpt or '')[:280]}{url}"
        )
    if detail.roster:
        if detail.roster.memo and detail.roster.memo.get("one_liner"):
            lines.append(f"Memo: {detail.roster.memo.get('one_liner')}")
        if detail.roster.earnings and detail.roster.earnings.get(
            "earnings_summary"
        ):
            lines.append(
                f"Earnings: {detail.roster.earnings.get('earnings_summary')}"
            )
        if detail.roster.macro and detail.roster.macro.get("macro_summary"):
            lines.append(f"Macro: {detail.roster.macro.get('macro_summary')}")
    return "\n".join(lines)


def _related_context(
    db: Session,
    *,
    owner_key: str,
    question: str,
    current_id: int,
) -> tuple[str, List[str]]:
    sources: List[str] = []
    chunks: List[str] = []
    try:
        result = investigation_search.search_investigations(
            db,
            owner_key=owner_key,
            query=question,
            limit=4,
        )
        for hit in result.get("results") or []:
            inv_id = hit.get("investigation_id")
            if inv_id == current_id:
                continue
            snippet = (hit.get("snippet") or hit.get("summary") or "")[:400]
            ticker = hit.get("ticker") or "?"
            chunks.append(f"Related case ${ticker} (id={inv_id}): {snippet}")
            if "related_cases" not in sources:
                sources.append("related_cases")
            if hit.get("match_sources") and "vector_memory" in (
                hit.get("match_sources") or []
            ):
                if "vector_memory" not in sources:
                    sources.append("vector_memory")
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "Investigation chat RAG search skipped",
            error=str(exc),
        )
    return "\n".join(chunks), sources


def _heuristic_answer(detail: InvestigationDetail, question: str) -> str:
    q = (question or "").lower()
    claims = sorted(detail.claims or [], key=lambda c: c.rank or 999)
    lead = claims[0].statement if claims else None
    move = detail.move_pct
    window = detail.window_label or "the window"
    move_line = (
        f"{detail.ticker} moved {move:+.1f}% over {window}."
        if move is not None
        else f"{detail.ticker} move over {window} was not cleanly measured."
    )
    if any(k in q for k in ("why", "cause", "caused", "reason")):
        cause = lead or detail.summary or "No leading hypothesis yet."
        return f"{move_line} Leading explanation: {cause}"
    if any(k in q for k in ("evidence", "source", "link", "proof")):
        if not detail.evidence_items:
            return "No evidence receipts are attached to this case yet."
        bits = []
        for ev in detail.evidence_items[:5]:
            bit = ev.title or "Untitled"
            if ev.source_url:
                bit += f" — {ev.source_url}"
            bits.append(bit)
        return "Key receipts:\n- " + "\n- ".join(bits)
    if lead:
        return f"{move_line} Best current answer from this case: {lead}"
    return (
        f"{move_line} "
        + (detail.summary or "Open Smart Summarize or re-run the investigation for more context.")
    )


async def answer_investigation_question(
    db: Session,
    *,
    owner_key: str,
    detail: InvestigationDetail,
    question: str,
) -> ChatResponse:
    symbol = detail.ticker.upper()
    sources_used: List[str] = ["current_case"]
    dossier = _case_dossier(detail)
    related, related_sources = _related_context(
        db,
        owner_key=owner_key,
        question=question,
        current_id=detail.id,
    )
    sources_used.extend(related_sources)

    context = dossier
    if related:
        context += "\n\nRelated ledger hits:\n" + related

    fallback = _heuristic_answer(detail, question)
    answer = await call_openai_text(
        system_prompt=(
            "You are QuantPilot's investigation RAG assistant. Answer using ONLY "
            "the Evidence Ledger context provided (current case + related hits). "
            "Prefer the move %, leading claim, and linked evidence. "
            "If unsure, say what is missing. Keep answers concise (2-4 short "
            "paragraphs or bullets). Not financial advice."
        ),
        user_prompt=f"Context:\n{context}\n\nQuestion: {question}",
        fallback=fallback,
    )
    if answer == fallback and "llm" not in sources_used:
        sources_used.append("heuristic")
    else:
        sources_used.append("llm")

    return ChatResponse(
        ticker=symbol,
        answer=answer,
        sources_used=sources_used,
    )

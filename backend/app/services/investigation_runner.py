"""PRD v3 Phase 8 — run reactive investigation into the Evidence Ledger."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.agents import (
    devils_advocate_agent,
    hypothesis_ranker_agent,
    investigation_planner_agent,
    investigation_verification_agent,
)
from app.db.models import User
from app.mcp_client import MCPClientError, call_tool
from app.models.investigation_schemas import InvestigationCreateRequest, InvestigationDetail
from app.observability.logger import get_logger, log_event
from app.services import asset_class as asset_class_service
from app.services import (
    evidence_ledger_store,
    investigation_notifications,
    investigation_roster,
    trigger_logic,
)
from app.config import INVESTIGATION_GRAPH_ENABLED
from app.security.llm_budget import bind_investigation_budget
from app.security.request_context import set_investigation_id
from app.services.move_detector import MoveSnapshot, detect_move

logger = get_logger("investigation_runner")


async def investigate_ticker(
    db: Session,
    *,
    owner_key: str,
    user: Optional[User],
    ticker: str,
    trigger_reason: str = "on_demand",
    window_label: str = "1d",
    skip_if_noise: bool = False,
    use_trigger_gate: bool = False,
    move_snapshot: Optional[MoveSnapshot] = None,
) -> InvestigationDetail:
    """Create investigation and run the investigation pipeline."""
    body = InvestigationCreateRequest(
        ticker=ticker,
        trigger_reason=trigger_reason,  # type: ignore[arg-type]
        window_label=window_label,
        summary=f"Running investigation for {ticker.upper().strip()}…",
    )
    created = evidence_ledger_store.create_investigation(
        db, owner_key=owner_key, user=user, body=body
    )
    if created is None:
        raise RuntimeError("Failed to create investigation")
    return await run_investigation(
        db,
        owner_key=owner_key,
        user=user,
        investigation_id=created.id,
        skip_if_noise=skip_if_noise or use_trigger_gate,
        window_label=window_label,
        use_trigger_gate=use_trigger_gate,
        move_snapshot=move_snapshot,
    )


async def run_investigation(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    skip_if_noise: bool = False,
    window_label: Optional[str] = None,
    use_trigger_gate: bool = False,
    user: Optional[User] = None,
    move_snapshot: Optional[MoveSnapshot] = None,
) -> InvestigationDetail:
    detail = evidence_ledger_store.get_investigation(
        db, owner_key=owner_key, investigation_id=investigation_id
    )
    if detail is None:
        raise LookupError("Investigation not found")

    symbol = detail.ticker
    window = window_label or detail.window_label or "1d"
    set_investigation_id(investigation_id)
    bind_investigation_budget(investigation_id)
    log_event(
        logger,
        logging.INFO,
        "INVESTIGATION_STARTED",
        event="INVESTIGATION_STARTED",
        ticker=symbol,
        investigation_id=investigation_id,
        owner_key=owner_key,
    )

    try:
        evidence_ledger_store.set_status(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            status="planning",
            summary=f"Detecting move for {symbol}…",
        )

        if move_snapshot is not None:
            move = move_snapshot
            if not getattr(move, "asset_class", None):
                move.asset_class = asset_class_service.classify_asset(symbol)
        elif use_trigger_gate:
            decision = await trigger_logic.evaluate_trigger(symbol, window=window)
            move = decision.move
            if not decision.should_investigate:
                return _persist_noise_skip(
                    db,
                    owner_key=owner_key,
                    investigation_id=investigation_id,
                    move=move,
                    reason=decision.reason,
                )
        else:
            move = await detect_move(symbol, window=window)
            move.asset_class = asset_class_service.classify_asset(symbol)
            if skip_if_noise and move.is_noise:
                return _persist_noise_skip(
                    db,
                    owner_key=owner_key,
                    investigation_id=investigation_id,
                    move=move,
                )

        evidence_ledger_store.update_move_metadata(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            move_pct=move.move_pct,
            window_label=move.window_label,
            summary=move.detail,
        )

        move_prefix = (move.detail or "").strip()
        graph_result = None
        memory_payload: Dict[str, Any] = {}
        try:
            from app.services import company_memory as company_memory_service

            memory_payload = company_memory_service.retrieve_for_investigation(
                db,
                owner_key=owner_key,
                ticker=symbol,
                event={"event_type": "PRICE_MOVE", "ticker": symbol},
                move_pct=move.move_pct,
                investigation_id=investigation_id,
            ).model_dump()
        except Exception as mem_exc:
            log_event(
                logger,
                logging.WARNING,
                "Company memory retrieval skipped",
                investigation_id=investigation_id,
                error=str(mem_exc),
            )
            memory_payload = {}
        if INVESTIGATION_GRAPH_ENABLED:
            from app.workflows.investigation_graph import run_investigation_graph

            evidence_ledger_store.set_status(
                db,
                owner_key=owner_key,
                investigation_id=investigation_id,
                status="collecting",
                summary=(
                    f"{move_prefix} Planning and collecting evidence…".strip()
                    if move_prefix
                    else "Planning and collecting evidence…"
                )[:2000],
            )
            graph_result = await run_investigation_graph(
                move,
                ticker=symbol,
                investigation_id=investigation_id,
                asset_class=move.asset_class or "equity",
                event={
                    "event_type": "PRICE_MOVE",
                    "ticker": symbol,
                    "magnitude": move.move_pct,
                    "residual_pct": getattr(move, "residual_pct", None),
                    "z_score": getattr(move, "move_zscore", None),
                },
                owner_key=owner_key,
                company_memory=memory_payload,
            )
            plan = graph_result.plan
            evidence_rows = graph_result.evidence_rows
            ranked = graph_result.ranked
            verified = graph_result.verified
            da = graph_result.da
            final_hypotheses = list(graph_result.hypotheses)
        else:
            plan = await investigation_planner_agent.plan_investigation(
                move,
                asset_hint=move.asset_class or "equity",
                company_memory=memory_payload,
            )
            collect_note = f"Collecting evidence: {plan.get('focus', '')[:200]}"
            evidence_ledger_store.set_status(
                db,
                owner_key=owner_key,
                investigation_id=investigation_id,
                status="collecting",
                summary=(
                    f"{move_prefix} {collect_note}".strip()
                    if move_prefix
                    else collect_note
                )[:2000],
            )
            evidence_rows = await _collect_evidence(move, plan)
            from app.services import evidence_graph as evidence_graph_service

            event = plan.get("event") if isinstance(plan.get("event"), dict) else {}
            raw_ts = (event or {}).get("occurred_at") or (event or {}).get(
                "detected_at"
            )
            event_time = None
            if raw_ts:
                from app.services import hypothesis_engine as _hyp_engine

                event_time = _hyp_engine._parse_time(raw_ts)
            evidence_rows, _ = evidence_graph_service.dedupe_items(
                evidence_rows,
                investigation_id=investigation_id,
                event_time=event_time,
            )
            ranked = await hypothesis_ranker_agent.rank_hypotheses(
                move, plan, evidence_rows
            )
            evidence_ledger_store.set_status(
                db,
                owner_key=owner_key,
                investigation_id=investigation_id,
                status="verifying",
                summary="Verification: grounding claims to evidence…",
            )
            linear_snap = evidence_graph_service.build_snapshot(
                ranked.get("hypotheses") or [],
                evidence_rows,
                investigation_id=investigation_id,
            )
            linear_ctx = evidence_graph_service.advocate_context(linear_snap)
            linear_ctx["claim_assessments"] = linear_snap.assessments
            linear_ctx["event"] = event
            linear_ctx["company_memory"] = memory_payload
            if memory_payload:
                from app.services.company_memory import historical_vs_current_note

                linear_ctx["historical_memory_note"] = historical_vs_current_note()
            verified = await investigation_verification_agent.verify_hypotheses(
                symbol,
                ranked.get("hypotheses") or [],
                evidence_rows,
                hypothesis_context=linear_ctx,
                investigation_id=investigation_id,
                event_time=event_time,
            )
            evidence_ledger_store.set_status(
                db,
                owner_key=owner_key,
                investigation_id=investigation_id,
                status="verifying",
                summary="Devil's Advocate: stress-testing the leading hypothesis…",
            )
            linear_ctx["verification_report"] = verified.get("verification_report") or {}
            da = await devils_advocate_agent.stress_test_leading(
                move,
                verified.get("hypotheses") or [],
                evidence_rows,
                hypothesis_context=linear_ctx,
            )
            final_hypotheses = da.get("hypotheses") or verified.get("hypotheses") or []

        evidence_ids = evidence_ledger_store.replace_system_evidence(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            items=evidence_rows,
        )

        # Hard enforce FR-3 again after DA reorder.
        final_hypotheses = [
            h for h in final_hypotheses if h.get("evidence_indices")
        ] or verified.get("hypotheses") or []

        evidence_ledger_store.replace_system_claims(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            hypotheses=final_hypotheses,
            evidence_ids=evidence_ids,
        )

        ver_notes = "; ".join(
            (verified.get("notes") or [])[:6]
            + (
                [f"Rejected {len(verified.get('rejected') or [])} unsupported claim(s)."]
                if verified.get("rejected")
                else []
            )
        )
        da_payload = {
            "outcome": da.get("outcome") or "held",
            "counterargument": da.get("counterargument") or "",
            "leading_weakened": bool(da.get("leading_weakened")),
            "reversal": bool(da.get("reversal")),
            "notes": da.get("notes") or [],
            "confidence_delta": float(da.get("confidence_delta") or 0),
            "citation_coverage": float(verified.get("citation_coverage") or 0),
            "challenge_severity": da.get("challenge_severity") or "",
            "recommendation": da.get("recommendation") or "",
            "alternative_hypothesis_id": da.get("alternative_hypothesis_id") or "",
            "additional_evidence_needed": da.get("additional_evidence_needed") or [],
            "questionable_assumptions": da.get("questionable_assumptions") or [],
            "conclusion_strength": (
                (graph_result.conclusion_strength if graph_result is not None else "")
                or (verified.get("verification_report") or {}).get("conclusion_strength")
                or ""
            ),
            "confidence": (
                ((graph_result.final_report or {}).get("confidence") if graph_result is not None else "")
                or ""
            ),
        }
        evidence_ledger_store.set_verification_audit(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            verification_notes=ver_notes,
            da_outcome=da_payload,
        )

        # PRD v3 Phase 13 — earnings / macro / investigation brief on collected evidence.
        evidence_ledger_store.set_status(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            status="verifying",
            summary="Roster pass: earnings, macro, investigation brief…",
        )
        try:
            roster = await investigation_roster.run_roster_pass(
                move,
                evidence_rows,
                hypotheses=final_hypotheses,
                verification_notes=ver_notes,
                da_outcome=da_payload,
            )
        except Exception as roster_exc:
            roster = {}
            log_event(
                logger,
                logging.WARNING,
                "Investigation roster pass failed",
                investigation_id=investigation_id,
                error=str(roster_exc),
            )
        if graph_result is not None:
            roster["investigation_graph"] = {
                "iteration_count": graph_result.iteration_count,
                "challenge_loops": graph_result.challenge_loops,
                "incomplete_evidence": graph_result.incomplete_evidence,
                "research_plan": graph_result.research_plan,
                "assessment": graph_result.assessment,
                "metrics": graph_result.metrics,
                "errors": graph_result.errors[:8],
                "engine": graph_result.engine,
                "verification_report": graph_result.verification_report,
                "final_report": graph_result.final_report,
                "conclusion_strength": graph_result.conclusion_strength,
                "evidence_gaps": graph_result.evidence_gaps,
            }
            roster["final_report"] = graph_result.final_report
            roster["verification_summary"] = (
                (graph_result.final_report or {}).get("verification") or {}
            )
            roster["evidence_gaps"] = graph_result.evidence_gaps
        else:
            from app.models.verification_schemas import VerificationReport
            from app.services import verification_engine as _v7

            try:
                vrep = VerificationReport.model_validate(
                    verified.get("verification_report") or {}
                )
            except Exception:
                vrep = None
            linear_report = _v7.build_final_report(
                move_line=f"{symbol} investigation",
                hypotheses=final_hypotheses,
                evidence=evidence_rows,
                verification=vrep,
                da=da_payload,
            )
            roster["final_report"] = linear_report.model_dump()
            roster["verification_summary"] = linear_report.verification.model_dump()
            roster["evidence_gaps"] = list(linear_report.evidence_gaps)
        from app.services import evidence_graph as evidence_graph_service

        snapshot = evidence_graph_service.build_snapshot(
            final_hypotheses,
            evidence_rows,
            investigation_id=investigation_id,
        )
        roster["evidence_graph"] = snapshot.model_dump()
        try:
            from app.services import company_memory as company_memory_service

            company_memory_service.extract_and_store(
                db,
                owner_key=owner_key,
                ticker=symbol,
                investigation_id=investigation_id,
                hypotheses=final_hypotheses,
                evidence=evidence_rows,
                evidence_ids=evidence_ids,
                move_pct=move.move_pct,
            )
            roster["company_memory"] = company_memory_service.build_summary(
                db, owner_key=owner_key, ticker=symbol
            ).model_dump()
            if memory_payload:
                roster.setdefault("investigation_graph", {})
                if isinstance(roster.get("investigation_graph"), dict):
                    roster["investigation_graph"]["company_memory"] = memory_payload
        except Exception as mem_store_exc:
            log_event(
                logger,
                logging.WARNING,
                "Company memory extraction skipped",
                investigation_id=investigation_id,
                error=str(mem_store_exc),
            )
        if roster:
            evidence_ledger_store.set_roster_context(
                db,
                owner_key=owner_key,
                investigation_id=investigation_id,
                roster=roster,
            )

        summary = _finalize_summary(
            move,
            ranked.get("summary") or "",
            da_payload,
            final_hypotheses,
            final_report=(graph_result.final_report if graph_result is not None else None),
        )
        if graph_result is not None and graph_result.incomplete_evidence:
            summary = (
                f"{summary} Evidence remains incomplete after the research loop."
            )[:2000]
        if graph_result is not None and graph_result.evidence_gaps:
            summary = (
                f"{summary} Evidence gaps: {'; '.join(graph_result.evidence_gaps[:3])}"
            )[:2000]
        completed = evidence_ledger_store.mark_complete(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            summary=summary,
        )
        if completed is None:
            raise RuntimeError("Failed to complete investigation")
        log_event(
            logger,
            logging.INFO,
            "INVESTIGATION_COMPLETED",
            event="INVESTIGATION_COMPLETED",
            ticker=symbol,
            investigation_id=investigation_id,
            claims=completed.claims_count,
            evidence=completed.evidence_count,
            da_outcome=da_payload.get("outcome"),
            da_reversal=da_payload.get("reversal"),
        )
        # PRD v3 Phase 12 — notify only above materiality bar (proactive cases).
        try:
            investigation_notifications.maybe_notify_investigation(
                db, user=user, detail=completed, move=move
            )
        except Exception as notify_exc:
            log_event(
                logger,
                logging.WARNING,
                "Investigation notification failed",
                investigation_id=investigation_id,
                error=str(notify_exc),
            )
        return completed
    except LookupError:
        raise
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "INVESTIGATION_FAILED",
            event="INVESTIGATION_FAILED",
            ticker=symbol,
            investigation_id=investigation_id,
            error=str(exc),
        )
        failed = evidence_ledger_store.mark_failed(
            db,
            owner_key=owner_key,
            investigation_id=investigation_id,
            error_message=str(exc),
        )
        if failed is None:
            raise
        return failed


def _persist_noise_skip(
    db: Session,
    *,
    owner_key: str,
    investigation_id: int,
    move: MoveSnapshot,
    reason: str = "",
) -> InvestigationDetail:
    skip_reason = reason or move.skip_reason or move.detail
    price_item = {
        "source_type": "price",
        "retrieval_method": move.source.split(":")[0] if move.source else "system",
        "title": f"{move.ticker} {move.window_label} move",
        "excerpt": skip_reason or move.detail,
        "source_url": "",
        "raw_payload": {
            "move_pct": move.move_pct,
            "window_label": move.window_label,
            "source": move.source,
            "residual_pct": getattr(move, "residual_pct", None),
            "move_zscore": getattr(move, "move_zscore", None),
            "asset_class": getattr(move, "asset_class", "equity"),
        },
    }
    evidence_ids = evidence_ledger_store.replace_system_evidence(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        items=[price_item],
    )
    evidence_ledger_store.replace_system_claims(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        hypotheses=[
            {
                "statement": (
                    skip_reason
                    or (
                        f"{move.ticker} move of {move.move_pct}% over {move.window_label} "
                        "is below the idiosyncratic / vol-adjusted bar — likely market noise."
                    )
                ),
                "stance": "market_noise",
                "confidence_score": 0.7,
                "weight": 1.0,
                "rank": 1,
                "devil_advocate_notes": (
                    "A thin news catalyst could still matter even on a small print."
                ),
                "evidence_indices": [0] if evidence_ids else [],
            }
        ],
        evidence_ids=evidence_ids,
    )
    result = evidence_ledger_store.mark_status(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        status="skipped_market_noise",
        summary=(
            f"{move.ticker}: {skip_reason}"
            if skip_reason
            else (
                f"{move.ticker} moved with little idiosyncratic signal "
                f"({move.move_pct}% / {move.window_label}). No deep investigation launched."
            )
        ),
        complete=True,
    )
    if result is None:
        raise RuntimeError("Failed to mark noise skip")
    return result


async def _collect_evidence(
    move: MoveSnapshot,
    plan: Dict[str, Any],
    *,
    escalate_thin: bool = True,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    items.append(
        {
            "source_type": "price",
            "retrieval_method": "mcp" if move.source.startswith("mcp") else "system",
            "title": f"{move.ticker} price move ({move.window_label})",
            "excerpt": move.detail,
            "source_url": "",
            "raw_payload": {
                "move_pct": move.move_pct,
                "current_price": move.current_price,
                "previous_close": move.previous_close,
                "source": move.source,
            },
        }
    )

    tools = list(plan.get("tools") or ["get_stock_news", "get_recent_filings"])
    from app.services.tool_router import filter_browser_budget

    collect_tools = [t for t in tools if t not in ("get_stock_price", "get_price_history")]
    collect_tools = filter_browser_budget(collect_tools)["tools"]
    chunks = await _collect_tools_parallel(move.ticker, collect_tools)
    for chunk in chunks:
        items.extend(chunk)

    # Ensure at least one non-price attempt via news if tools failed empty.
    if len(items) == 1 and "get_stock_news" not in tools:
        items.extend(await _evidence_from_tool(move.ticker, "get_stock_news"))

    # PRD Phase 10 / FR-5: when API evidence is thin, escalate to Browser MCP IR.
    # The LangGraph loop owns IR escalation when escalate_thin=False.
    if (
        escalate_thin
        and _api_evidence_thin(items)
        and investigation_planner_agent.browser_mcp_enabled()
    ):
        for tool in ("get_ir_materials", "get_shareholder_letter"):
            if tool in tools:
                continue
            log_event(
                logger,
                logging.INFO,
                "Escalating to Browser MCP for thin API evidence",
                ticker=move.ticker,
                tool=tool,
            )
            items.extend(await _evidence_from_tool(move.ticker, tool))

    return items[:24]


def _research_concurrency() -> int:
    return max(1, min(8, int(os.getenv("INVESTIGATION_RESEARCH_CONCURRENCY", "4"))))


def _tool_retries() -> int:
    return max(0, min(3, int(os.getenv("INVESTIGATION_TOOL_RETRIES", "1"))))


async def _collect_tools_parallel(
    ticker: str, tools: List[str]
) -> List[List[Dict[str, Any]]]:
    """Run independent MCP tool fetches concurrently with a small cap."""
    if not tools:
        return []
    sem = asyncio.Semaphore(_research_concurrency())

    async def _one(tool: str) -> List[Dict[str, Any]]:
        async with sem:
            return await _evidence_from_tool_with_retry(ticker, tool)

    return list(await asyncio.gather(*[_one(t) for t in tools]))


async def _evidence_from_tool_with_retry(ticker: str, tool: str) -> List[Dict[str, Any]]:
    last: List[Dict[str, Any]] = []
    attempts = 1 + _tool_retries()
    for attempt in range(attempts):
        last = await _evidence_from_tool(ticker, tool)
        if not last:
            return last
        if not all(
            (row.get("raw_payload") or {}).get("error")
            or "unavailable" in (row.get("title") or "").lower()
            or "failed" in (row.get("title") or "").lower()
            for row in last
        ):
            return last
        if attempt + 1 < attempts:
            log_event(
                logger,
                logging.INFO,
                "RESEARCH_TASK_FAILED",
                event="RESEARCH_TASK_FAILED",
                tool=tool,
                ticker=ticker,
                attempt=attempt + 1,
                retrying=True,
            )
            await asyncio.sleep(0.05)
    return last


def _api_evidence_thin(items: List[Dict[str, Any]]) -> bool:
    news = [i for i in items if i.get("source_type") == "news"]
    filings = [i for i in items if i.get("source_type") == "filing"]
    ir = [i for i in items if i.get("source_type") == "ir_page"]
    if ir:
        return False
    substantive_news = [
        n
        for n in news
        if n.get("title") and "unavailable" not in (n.get("title") or "").lower()
    ]
    return len(substantive_news) < 2 and len(filings) < 1


async def _evidence_from_tool(ticker: str, tool: str) -> List[Dict[str, Any]]:
    try:
        result = await call_tool(
            tool,
            _tool_args(ticker, tool),
        )
    except MCPClientError as exc:
        return [
            {
                "source_type": "other",
                "retrieval_method": "mcp",
                "title": f"{tool} unavailable",
                "excerpt": str(exc)[:500],
                "source_url": "",
                "raw_payload": {"tool": tool, "error": str(exc)},
            }
        ]
    except Exception as exc:
        return [
            {
                "source_type": "other",
                "retrieval_method": "system",
                "title": f"{tool} failed",
                "excerpt": str(exc)[:500],
                "source_url": "",
                "raw_payload": {"tool": tool, "error": str(exc)},
            }
        ]

    if not isinstance(result, dict):
        return []

    if tool == "get_stock_news":
        out: List[Dict[str, Any]] = []
        for h in (result.get("headlines") or [])[:8]:
            title = h.get("title") or "Headline"
            link = ""
            raw_link = h.get("link")
            if isinstance(raw_link, dict):
                link = str(raw_link.get("url") or "")[:1024]
            elif raw_link:
                link = str(raw_link)[:1024]
            out.append(
                {
                    "source_type": "news",
                    "retrieval_method": "mcp",
                    "title": str(title)[:280],
                    "excerpt": f"{h.get('publisher') or 'News'}: {title}",
                    "source_url": link
                    or f"https://finance.yahoo.com/quote/{ticker}/news",
                    "raw_payload": h,
                }
            )
        return out

    if tool == "get_recent_filings":
        out = []
        for f in (result.get("filings") or result.get("items") or [])[:5]:
            form_type = f.get("form_type") or f.get("form") or "filing"
            title = f"{form_type} · {f.get('filing_date') or f.get('date') or ''}".strip()
            out.append(
                {
                    "source_type": "filing",
                    "retrieval_method": "mcp",
                    "title": title[:280],
                    "excerpt": str(
                        f.get("description")
                        or f.get("report_date")
                        or f.get("accession_number")
                        or title
                    )[:2000],
                    "source_url": str(
                        f.get("document_url")
                        or f.get("url")
                        or f.get("filing_url")
                        or f"https://www.sec.gov/edgar/search/#/entityName={ticker}"
                    )[:1024],
                    "raw_payload": f,
                }
            )
        if not out and result.get("error"):
            out.append(
                {
                    "source_type": "filing",
                    "retrieval_method": "mcp",
                    "title": "Filings lookup",
                    "excerpt": str(result.get("error"))[:500],
                    "source_url": f"https://www.sec.gov/edgar/search/#/entityName={ticker}",
                    "raw_payload": result,
                }
            )
        return out

    if tool in (
        "get_ir_materials",
        "get_shareholder_letter",
        "fetch_browser_page",
        "browser_find_document",
        "browser_search",
        "browser_open",
        "browser_download",
        "browser_extract",
    ):
        return _map_browser_evidence(ticker, tool, result)

    if tool == "get_stock_fundamentals":
        return [
            {
                "source_type": "other",
                "retrieval_method": "mcp",
                "title": f"{ticker} fundamentals snapshot",
                "excerpt": json.dumps(result, default=str)[:2000],
                "source_url": f"https://finance.yahoo.com/quote/{ticker}/key-statistics",
                "raw_payload": result,
            }
        ]

    if tool == "get_stock_price":
        return [
            {
                "source_type": "price",
                "retrieval_method": "mcp",
                "title": f"{ticker} price snapshot",
                "excerpt": json.dumps(result, default=str)[:2000],
                "source_url": f"https://finance.yahoo.com/quote/{ticker}",
                "raw_payload": result,
            }
        ]

    if tool == "get_price_history":
        period = str(result.get("period") or "1mo")
        return [
            {
                "source_type": "price",
                "retrieval_method": "mcp",
                "title": f"{ticker} price history ({period})",
                "excerpt": json.dumps(
                    {"period": period, "points": len(result.get("history") or [])},
                    default=str,
                )[:2000],
                "source_url": f"https://finance.yahoo.com/quote/{ticker}/history",
                "raw_payload": {"period": period, "count": len(result.get("history") or [])},
            }
        ]

    return [
        {
            "source_type": "other",
            "retrieval_method": "mcp",
            "title": tool,
            "excerpt": json.dumps(result, default=str)[:2000],
            "source_url": "",
            "raw_payload": result,
        }
    ]


def _map_browser_evidence(
    ticker: str, tool: str, result: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Map Browser MCP / OpenClaw tool payloads into ledger evidence rows."""
    if result.get("enabled") is False:
        return [
            {
                "source_type": "ir_page",
                "retrieval_method": "system",
                "title": f"{tool} disabled",
                "excerpt": str(result.get("error") or "Browser MCP disabled")[:500],
                "source_url": "",
                "raw_payload": result,
            }
        ]

    out: List[Dict[str, Any]] = []

    if tool in {
        "browser_find_document",
        "browser_search",
        "browser_open",
        "browser_download",
        "browser_extract",
    }:
        return _map_browser_contract_evidence(ticker, tool, result)

    if tool == "get_shareholder_letter":
        if result.get("found") and (result.get("text_excerpt") or result.get("url")):
            provider = str(result.get("provider") or "httpx")
            out.append(
                {
                    "source_type": "ir_page",
                    "retrieval_method": _provider_to_method(provider),
                    "title": str(
                        result.get("title") or f"{ticker} shareholder letter"
                    )[:280],
                    "excerpt": str(result.get("text_excerpt") or "")[:4000],
                    "source_url": str(result.get("url") or "")[:1024],
                    "raw_payload": result,
                }
            )
            return out
        return [
            {
                "source_type": "ir_page",
                "retrieval_method": "mcp",
                "title": f"{ticker} shareholder letter not found",
                "excerpt": str(result.get("error") or "No letter matched")[:500],
                "source_url": "",
                "raw_payload": result,
            }
        ]

    if tool == "fetch_browser_page":
        if result.get("text_excerpt") or result.get("url"):
            provider = str(result.get("provider") or "httpx")
            out.append(
                {
                    "source_type": "ir_page",
                    "retrieval_method": _provider_to_method(provider),
                    "title": str(result.get("title") or result.get("url") or "Page")[
                        :280
                    ],
                    "excerpt": str(result.get("text_excerpt") or "")[:4000],
                    "source_url": str(result.get("url") or "")[:1024],
                    "raw_payload": result,
                }
            )
        elif result.get("error"):
            out.append(
                {
                    "source_type": "ir_page",
                    "retrieval_method": "mcp",
                    "title": "Browser page fetch failed",
                    "excerpt": str(result.get("error"))[:500],
                    "source_url": str(result.get("url") or "")[:1024],
                    "raw_payload": result,
                }
            )
        return out

    # get_ir_materials — real shape uses pages[] / excerpt / sources
    pages = result.get("pages") or []
    if isinstance(pages, list):
        for page in pages[:4]:
            if not isinstance(page, dict):
                continue
            provider = str(page.get("provider") or result.get("provider") or "httpx")
            excerpt = str(
                page.get("text_excerpt") or page.get("excerpt") or ""
            )[:4000]
            if not excerpt and not page.get("url"):
                continue
            out.append(
                {
                    "source_type": "ir_page",
                    "retrieval_method": _provider_to_method(provider),
                    "title": str(page.get("title") or f"{ticker} IR page")[:280],
                    "excerpt": excerpt or "(no excerpt)",
                    "source_url": str(page.get("url") or "")[:1024],
                    "raw_payload": page,
                }
            )

    if not out and result.get("excerpt"):
        provider = str(result.get("provider") or "httpx")
        sources = result.get("sources") or []
        out.append(
            {
                "source_type": "ir_page",
                "retrieval_method": _provider_to_method(provider),
                "title": f"{ticker} IR materials",
                "excerpt": str(result.get("excerpt"))[:4000],
                "source_url": str(sources[0] if sources else "")[:1024],
                "raw_payload": {
                    "provider": provider,
                    "sources": sources,
                    "error": result.get("error"),
                },
            }
        )

    if not out:
        out.append(
            {
                "source_type": "ir_page",
                "retrieval_method": "mcp",
                "title": f"{ticker} IR materials empty",
                "excerpt": str(result.get("error") or "No IR pages retrieved")[:500],
                "source_url": "",
                "raw_payload": result,
            }
        )
    return out


def _map_browser_contract_evidence(
    ticker: str, tool: str, result: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Map Phase 6 Browser MCP tools into the existing ledger shape."""
    payload = dict(result or {})
    payload["tool"] = tool
    error = str(result.get("error") or "")
    if result.get("ok") is False or (error and not (result.get("text") or result.get("url"))):
        return [
            {
                "source_type": "ir_page",
                "retrieval_method": "system",
                "title": f"{ticker} primary-source {tool} failed",
                "excerpt": error[:500] or "Browser MCP returned no official document.",
                "source_url": str(result.get("url") or "")[:1024],
                "raw_payload": payload,
            }
        ]

    provider = str(result.get("provider") or "httpx")
    method = "browser_mcp"
    if "httpx" in provider and not result.get("used_openclaw"):
        method = "httpx"
    elif "openclaw" in provider or result.get("used_openclaw"):
        method = "browser_mcp"

    selected = result.get("selected") if isinstance(result.get("selected"), dict) else {}
    url = str(
        result.get("url")
        or selected.get("url")
        or ((result.get("candidates") or [{}])[0].get("url") if result.get("candidates") else "")
        or ""
    )[:1024]
    title = str(
        result.get("title")
        or selected.get("title")
        or f"{ticker} official document"
    )[:280]
    excerpt = str(result.get("text") or result.get("text_excerpt") or "")[:4000]
    if result.get("extraction_failed") and not excerpt:
        excerpt = "Official document located but text extraction failed."
        payload["error"] = payload.get("error") or "extraction_failed"
        return [
            {
                "source_type": "ir_page",
                "retrieval_method": method,
                "title": f"{title} (extraction failed)",
                "excerpt": excerpt,
                "source_url": url,
                "raw_payload": payload,
            }
        ]
    if not excerpt and not url:
        return [
            {
                "source_type": "ir_page",
                "retrieval_method": "system",
                "title": f"{ticker} primary-source empty",
                "excerpt": error[:500] or "No official content returned.",
                "source_url": "",
                "raw_payload": payload,
            }
        ]
    return [
        {
            "source_type": "ir_page",
            "retrieval_method": method,
            "title": title,
            "excerpt": excerpt or "(official document; no extractable text)",
            "source_url": url,
            "raw_payload": payload,
        }
    ]


def _provider_to_method(provider: str) -> str:
    value = (provider or "").lower()
    if "browser_mcp" in value:
        return "browser_mcp"
    if "openclaw" in value:
        return "openclaw"
    if "httpx" in value:
        return "httpx"
    if value in {"disabled", "error", "none", "not_configured"}:
        return "system"
    return "mcp"


def _human_window(window_label: str) -> str:
    mapping = {
        "1d": "1 day",
        "intraday": "1 day",
        "1w": "1 week",
        "5d": "1 week",
        "1mo": "1 month",
        "3mo": "3 months",
        "6mo": "6 months",
        "1y": "1 year",
        "ytd": "year to date",
    }
    key = (window_label or "1d").strip().lower()
    return mapping.get(key, window_label or "the selected window")


def _finalize_summary(
    move: MoveSnapshot,
    base: str,
    da_payload: Dict[str, Any],
    hypotheses: List[Dict[str, Any]],
    final_report: Optional[Dict[str, Any]] = None,
) -> str:
    """Lead with move size, then cause, then DA check — matches Evidence Ledger UX."""
    window = _human_window(move.window_label or "1d")
    ticker = move.ticker or "Ticker"
    move_pct = move.move_pct
    if move_pct is None:
        move_line = f"{ticker} move over {window} could not be measured cleanly."
    else:
        abs_move = abs(float(move_pct))
        if float(move_pct) < -0.05:
            move_line = f"{ticker} fell {abs_move:.1f}% over {window}."
        elif float(move_pct) > 0.05:
            move_line = f"{ticker} rose {abs_move:.1f}% over {window}."
        else:
            sign = "+" if float(move_pct) >= 0 else ""
            move_line = (
                f"{ticker} was roughly flat ({sign}{float(move_pct):.1f}%) over {window}."
            )

    lead = hypotheses[0]["statement"] if hypotheses else "n/a"
    lead_row = hypotheses[0] if hypotheses else {}
    from app.services import evidence_graph as _eg

    claim_kind = _eg.infer_claim_type(str(lead)) if hypotheses else ""
    outcome = da_payload.get("outcome") or "held"
    da_line = {
        "held": "Devil's Advocate did not overturn the leading hypothesis.",
        "confidence_cut": "Devil's Advocate weakened the leading hypothesis (confidence cut).",
        "demoted": "Devil's Advocate demoted the leading hypothesis; a competitor was promoted.",
    }.get(str(outcome), f"Devil's Advocate outcome: {outcome}.")
    severity = str(da_payload.get("challenge_severity") or "").strip()
    if severity:
        da_line = f"{da_line} Challenge severity: {severity}."
    rec = str(da_payload.get("recommendation") or "").strip()
    if rec:
        da_line = f"{da_line} Recommendation: {rec}."
    counter = str(da_payload.get("counterargument") or "").strip()
    if counter and counter not in da_line:
        da_line = f"{da_line} Opposing case: {counter[:280]}"
    support_line = ""
    level = lead_row.get("confidence_level")
    strength = str(
        (final_report or {}).get("conclusion_strength")
        or da_payload.get("conclusion_strength")
        or ""
    )
    confidence = str((final_report or {}).get("confidence") or da_payload.get("confidence") or level or "")
    if strength:
        support_line = f"Conclusion strength: {strength}."
        if confidence:
            support_line = f"{support_line} Confidence: {confidence} (not a calibrated probability)."
    elif level:
        support_line = f"Primary explanation support: {level} (not a calibrated probability)."
    contra = lead_row.get("contradicting_indices") or []
    if contra:
        support_line = (
            f"{support_line} Counterevidence is recorded against the lead."
        ).strip()
    alt = ""
    if len(hypotheses) > 1:
        alt = f"Strongest alternative: {hypotheses[1].get('statement')}."
    type_line = (
        f"Claim type: {claim_kind}. Causal claims are supported, not proven."
        if claim_kind == "CAUSAL_EXPLANATION"
        else (f"Claim type: {claim_kind}." if claim_kind else "")
    )
    lead_line = (
        f"Leading cause: {lead}"
        if strength != "INSUFFICIENT_EVIDENCE"
        else "Insufficient evidence to determine the primary cause."
    )
    gaps = list((final_report or {}).get("evidence_gaps") or [])
    gap_line = f"Evidence gaps: {'; '.join(gaps[:3])}" if gaps else ""
    parts = [
        move_line,
        lead_line,
        type_line,
        support_line,
        alt,
        (base or "").strip(),
        da_line,
        gap_line,
    ]
    # Drop empty / duplicate fragments
    cleaned: List[str] = []
    seen = set()
    for part in parts:
        text = (part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return " ".join(cleaned)[:2000]


def _tool_args(ticker: str, tool: str) -> Dict[str, Any]:
    if tool == "get_stock_news":
        return {"ticker": ticker, "limit": 8}
    if tool == "get_recent_filings":
        return {"ticker": ticker, "limit": 5}
    if tool == "get_price_history":
        return {"ticker": ticker, "period": "1mo"}
    if tool == "get_ir_materials":
        return {"ticker": ticker, "max_pages": 2}
    if tool == "get_shareholder_letter":
        return {"ticker": ticker}
    if tool == "browser_find_document":
        return {
            "ticker": ticker,
            "document_type": "investor_presentation",
            "query": "latest official investor presentation",
        }
    if tool == "browser_search":
        return {"domain": "", "query": ticker, "ticker": ticker}
    if tool in ("browser_open", "browser_download"):
        return {"url": "", "ticker": ticker}
    if tool == "browser_extract":
        return {"text": "", "objective": "guidance", "source_url": ""}
    return {"ticker": ticker}

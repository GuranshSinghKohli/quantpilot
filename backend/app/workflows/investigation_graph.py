"""Phase 3 — LangGraph investigation orchestrator with a bounded research loop."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.investigation_planner_agent import (
    browser_mcp_enabled,
    research_plan_from_legacy,
    tasks_from_tools,
    validate_research_plan,
)
from app.config import MAX_CHALLENGE_LOOPS, MAX_INVESTIGATION_ITERATIONS
from app.models.investigation_plan_schemas import (
    EvidenceAssessment,
    InvestigationGraphResult,
    ResearchTask,
)
from app.observability.logger import get_logger, log_event
from app.services.evidence_assessment import assess_evidence
from app.services.move_detector import MoveSnapshot

logger = get_logger("investigation_graph")


class InvestigationState(TypedDict, total=False):
    investigation_id: int
    ticker: str
    event: Dict[str, Any]
    event_type: str
    investigation_depth: str
    materiality_score: float
    portfolio_exposure: float
    asset_class: str
    move: MoveSnapshot
    plan: Dict[str, Any]
    research_plan: Dict[str, Any]
    hypotheses: List[str]
    selected_hypothesis: str
    hypothesis_rows: List[Dict[str, Any]]
    evidence_items: List[Dict[str, Any]]
    tasks: List[Dict[str, Any]]
    assessment: Dict[str, Any]
    ranked: Dict[str, Any]
    verified: Dict[str, Any]
    da: Dict[str, Any]
    status: str
    iteration_count: int
    errors: List[str]
    metrics: Dict[str, Any]
    incomplete_evidence: bool
    sufficient_evidence: bool
    hypothesis_records: List[Dict[str, Any]]
    engine_result: Dict[str, Any]
    challenge_loops: int
    needs_challenge_research: bool
    final_report: Dict[str, Any]
    owner_key: str
    company_memory: Dict[str, Any]


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _log(event: str, state: InvestigationState, **extra: Any) -> None:
    iteration = extra.pop("iteration", state.get("iteration_count", 0))
    log_event(
        logger,
        logging.INFO,
        event,
        event=event,
        investigation_id=state.get("investigation_id"),
        ticker=state.get("ticker"),
        event_type=state.get("event_type"),
        graph_node=extra.pop("graph_node", None),
        iteration=iteration,
        **extra,
    )


def _dedupe_evidence(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        key = (
            item.get("source_type"),
            item.get("source_url"),
            item.get("title"),
            (item.get("excerpt") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:24]


def _pending_tools(tasks: List[Dict[str, Any]]) -> List[str]:
    tools: List[str] = []
    seen = set()
    for task in tasks:
        if task.get("status") != "pending":
            continue
        tool = task.get("required_tool")
        if tool and tool not in seen:
            seen.add(tool)
            tools.append(str(tool))
    return tools


async def load_memory_node(state: InvestigationState) -> InvestigationState:
    """Load owner-scoped company memory. Fail-open. Never injects portfolio data."""
    existing = state.get("company_memory") or {}
    if existing:
        _log(
            "MEMORY_CONTEXT_ADDED",
            state,
            graph_node="load_memory",
            relevance=len(existing.get("items") or []),
        )
        return {**state, "company_memory": existing}
    owner = str(state.get("owner_key") or "")
    ticker = str(state.get("ticker") or "")
    if not owner or not ticker:
        return {**state, "company_memory": {}}
    try:
        from app.db.session import SessionLocal
        from app.services import company_memory

        db = SessionLocal()
        try:
            ctx = company_memory.retrieve_for_investigation(
                db,
                owner_key=owner,
                ticker=ticker,
                event=state.get("event") or {},
                hypotheses=list(state.get("hypotheses") or []),
                move_pct=getattr(state.get("move"), "move_pct", None),
                investigation_id=int(state.get("investigation_id") or 0),
            )
            payload = ctx.model_dump()
        finally:
            db.close()
        _log(
            "MEMORY_CONTEXT_ADDED",
            state,
            graph_node="load_memory",
            relevance=len(payload.get("items") or []),
        )
        return {**state, "company_memory": payload}
    except Exception as exc:
        errors = list(state.get("errors") or [])
        errors.append(f"company_memory: {exc}")
        return {**state, "company_memory": {}, "errors": errors}


async def planner_node(state: InvestigationState) -> InvestigationState:
    from app.services import investigation_runner as runner

    start = _now_ms()
    move = state["move"]
    _log("INVESTIGATION_STARTED", state, graph_node="planner")
    try:
        plan = await runner.investigation_planner_agent.plan_investigation(
            move,
            asset_hint=state.get("asset_class") or "equity",
            event=state.get("event") or {},
            materiality_score=float(state.get("materiality_score") or 0),
            portfolio_exposure=float(state.get("portfolio_exposure") or 0),
            company_memory=state.get("company_memory") or {},
        )
        plan["event"] = state.get("event") or {}
        structured = research_plan_from_legacy(
            plan, move=move, asset_hint=state.get("asset_class") or "equity"
        )
        metrics = dict(state.get("metrics") or {})
        metrics["planner_ms"] = _now_ms() - start
        _log(
            "INVESTIGATION_PLANNED",
            state,
            graph_node="planner",
            tools=structured.tools,
            hypothesis_count=len(structured.hypotheses),
            latency_ms=metrics["planner_ms"],
        )
        records = list(plan.get("hypothesis_records") or [])
        return {
            **state,
            "plan": plan,
            "research_plan": structured.model_dump(),
            "hypotheses": structured.hypotheses,
            "hypothesis_records": records,
            "status": "planning",
            "metrics": metrics,
        }
    except Exception as exc:
        errors = list(state.get("errors") or [])
        errors.append(f"planner: {exc}")
        fallback = runner.investigation_planner_agent._fallback_plan(
            move, asset_hint=state.get("asset_class") or "equity"
        )
        from app.agents.hypothesis_generator import catalog_hypotheses

        fallback_records = catalog_hypotheses(
            move,
            asset_hint=state.get("asset_class") or "equity",
            event=state.get("event") or {},
            seeds=list(fallback.get("seed_hypotheses") or []),
        )
        fallback["hypothesis_records"] = [r.model_dump() for r in fallback_records]
        fallback["event"] = state.get("event") or {}
        structured = research_plan_from_legacy(fallback, move=move)
        return {
            **state,
            "plan": fallback,
            "research_plan": structured.model_dump(),
            "hypotheses": structured.hypotheses,
            "hypothesis_records": [r.model_dump() for r in fallback_records],
            "errors": errors,
            "status": "planning",
        }


async def create_tasks_node(state: InvestigationState) -> InvestigationState:
    plan = state.get("research_plan") or {}
    try:
        structured = validate_research_plan(plan)
    except Exception:
        structured = research_plan_from_legacy(
            state.get("plan") or {},
            move=state.get("move"),
            asset_hint=state.get("asset_class") or "equity",
        )
    tasks = [t.model_dump() for t in structured.tasks]
    for task in tasks:
        _log(
            "RESEARCH_TASK_CREATED",
            state,
            graph_node="create_tasks",
            task_id=task.get("task_id"),
            tool=task.get("required_tool"),
        )
    return {
        **state,
        "research_plan": structured.model_dump(),
        "tasks": tasks,
        "hypotheses": structured.hypotheses or state.get("hypotheses") or [],
        "status": "collecting",
    }


async def research_node(state: InvestigationState) -> InvestigationState:
    from app.services import investigation_runner as runner

    start = _now_ms()
    move = state["move"]
    tasks = [dict(t) for t in (state.get("tasks") or [])]
    pending = [t for t in tasks if t.get("status") == "pending"]
    tools = _pending_tools(pending)
    iteration = int(state.get("iteration_count") or 0) + 1

    for task in pending:
        task["status"] = "running"
        _log(
            "RESEARCH_TASK_STARTED",
            state,
            graph_node="research",
            task_id=task.get("task_id"),
            tool=task.get("required_tool"),
            iteration=iteration,
        )

    plan = dict(state.get("plan") or {})
    plan["tools"] = tools
    try:
        collected = await runner._collect_evidence(move, plan, escalate_thin=False)
    except Exception as exc:
        collected = [
            {
                "source_type": "other",
                "retrieval_method": "system",
                "title": "research batch failed",
                "excerpt": str(exc)[:500],
                "source_url": "",
                "raw_payload": {"error": str(exc)},
            }
        ]
    from app.services import evidence_graph, hypothesis_engine

    evidence = _dedupe_evidence(list(state.get("evidence_items") or []) + collected)
    event_time = hypothesis_engine._parse_time(
        (state.get("event") or {}).get("occurred_at")
        or (state.get("event") or {}).get("detected_at")
    )
    evidence, _ = evidence_graph.dedupe_items(
        evidence,
        investigation_id=int(state.get("investigation_id") or 0),
        event_time=event_time,
    )

    by_tool_ok = _tools_with_success(collected)
    errors = list(state.get("errors") or [])
    for task in pending:
        tool = task.get("required_tool")
        if tool in by_tool_ok:
            task["status"] = "completed"
            _log(
                "RESEARCH_TASK_COMPLETED",
                state,
                graph_node="research",
                task_id=task.get("task_id"),
                tool=tool,
            )
        else:
            task["status"] = "failed"
            task["error"] = f"{tool} returned no usable evidence"
            errors.append(task["error"])
            _log(
                "RESEARCH_TASK_FAILED",
                state,
                graph_node="research",
                task_id=task.get("task_id"),
                tool=tool,
            )

    metrics = dict(state.get("metrics") or {})
    metrics["research_ms"] = metrics.get("research_ms", 0) + (_now_ms() - start)
    metrics["tool_calls"] = int(metrics.get("tool_calls") or 0) + len(tools)
    metrics["iterations"] = iteration
    engine_payload, records = _refresh_engine(state, evidence, iteration)
    return {
        **state,
        "tasks": tasks,
        "evidence_items": evidence,
        "iteration_count": iteration,
        "errors": errors,
        "metrics": metrics,
        "status": "collecting",
        "engine_result": engine_payload,
        "hypothesis_records": records,
    }


def _refresh_engine(
    state: InvestigationState,
    evidence: List[Dict[str, Any]],
    iteration: int,
):
    from app.models.hypothesis_schemas import HypothesisRecord
    from app.services import hypothesis_engine

    raw = state.get("hypothesis_records") or (state.get("plan") or {}).get(
        "hypothesis_records"
    ) or []
    records = []
    for row in raw:
        try:
            records.append(HypothesisRecord.model_validate(row))
        except Exception:
            continue
    if not records:
        from app.agents.hypothesis_generator import catalog_hypotheses

        records = catalog_hypotheses(
            state["move"],
            asset_hint=state.get("asset_class") or "equity",
            event=state.get("event") or {},
            seeds=list(state.get("hypotheses") or []),
        )
    event_time = hypothesis_engine._parse_time(
        (state.get("event") or {}).get("occurred_at")
        or (state.get("event") or {}).get("detected_at")
    )
    result = hypothesis_engine.score_and_rank(
        records,
        evidence,
        event_time=event_time,
        move=state.get("move"),
        investigation_id=int(state.get("investigation_id") or 0),
        ticker=str(state.get("ticker") or ""),
        iteration=iteration,
    )
    log_event(
        logger,
        logging.INFO,
        "EVIDENCE_CLASSIFIED",
        event="EVIDENCE_CLASSIFIED",
        investigation_id=state.get("investigation_id"),
        ticker=state.get("ticker"),
        iteration=iteration,
        classifications=len(result.classifications),
    )
    return result.model_dump(), [r.model_dump() for r in result.hypotheses]


def _tools_with_success(items: List[Dict[str, Any]]) -> set:
    ok = set()
    for item in items:
        payload = item.get("raw_payload") or {}
        tool = payload.get("tool")
        title = (item.get("title") or "").lower()
        failed = bool(payload.get("error")) or "unavailable" in title or "failed" in title
        if tool and not failed:
            ok.add(str(tool))
        source = item.get("source_type")
        if source == "news" and not failed:
            ok.add("get_stock_news")
        if source == "filing" and not failed:
            ok.add("get_recent_filings")
        if source == "ir_page" and not failed:
            ok.add("get_ir_materials")
            payload_tool = str(payload.get("tool") or "")
            if payload_tool.startswith("browser_"):
                ok.add(payload_tool)
        if source == "price":
            ok.add("get_stock_price")
    return ok


async def assess_node(state: InvestigationState) -> InvestigationState:
    tasks = [ResearchTask.model_validate(t) for t in (state.get("tasks") or [])]
    assessment = assess_evidence(
        state.get("evidence_items") or [],
        tasks=tasks,
        asset_hint=state.get("asset_class") or "equity",
        used_tools=[t.required_tool for t in tasks if t.status != "pending"],
        hypotheses=state.get("hypotheses") or [],
        browser_enabled=browser_mcp_enabled(),
        hypothesis_records=state.get("hypothesis_records") or [],
    )
    iteration = int(state.get("iteration_count") or 0)
    max_iter = int(state.get("metrics", {}).get("max_iterations") or MAX_INVESTIGATION_ITERATIONS)
    existing_tools = {t.required_tool for t in tasks}
    unused_recommended = [
        tool for tool in assessment.recommended_next_tools if tool not in existing_tools
    ]
    assessment.recommended_next_tools = unused_recommended
    can_loop = iteration < max_iter and bool(unused_recommended)
    sufficient = assessment.sufficient_evidence or not can_loop
    if not assessment.sufficient_evidence and not can_loop:
        assessment.notes.append(
            "Maximum investigation iterations reached; continuing with incomplete evidence."
        )
    payload = assessment.model_dump()
    _log(
        "EVIDENCE_ASSESSMENT",
        state,
        graph_node="assess",
        sufficient=assessment.sufficient_evidence,
        missing=assessment.missing_evidence,
        recommended=assessment.recommended_next_tools,
        iteration=iteration,
    )
    if not sufficient:
        _log(
            "ADDITIONAL_RESEARCH_REQUIRED",
            state,
            graph_node="assess",
            recommended=assessment.recommended_next_tools,
        )
    return {
        **state,
        "assessment": payload,
        "sufficient_evidence": sufficient,
        "incomplete_evidence": not assessment.sufficient_evidence,
        "status": "verifying" if sufficient else "collecting",
    }


async def identify_gaps_node(state: InvestigationState) -> InvestigationState:
    assessment = EvidenceAssessment.model_validate(state.get("assessment") or {})
    existing = [ResearchTask.model_validate(t) for t in (state.get("tasks") or [])]
    used = {t.required_tool for t in existing}
    start = len(existing) + 1
    from app.services.tool_router import filter_browser_budget, is_browser_tool

    raw_new = [t for t in assessment.recommended_next_tools if t not in used]
    already = sum(1 for t in existing if is_browser_tool(t.required_tool))
    budget = filter_browser_budget(raw_new, already_used=already)
    new_tools = budget["tools"]
    added = tasks_from_tools(
        new_tools,
        hypothesis_count=len(state.get("hypotheses") or []),
        start_index=start,
        hypothesis_records=state.get("hypothesis_records") or [],
    )
    merged = [t.model_dump() for t in existing] + [t.model_dump() for t in added]
    for task in added:
        _log(
            "RESEARCH_TASK_CREATED",
            state,
            graph_node="identify_gaps",
            task_id=task.task_id,
            tool=task.required_tool,
        )
    return {**state, "tasks": merged, "status": "collecting"}


async def rank_node(state: InvestigationState) -> InvestigationState:
    from app.services import investigation_runner as runner

    start = _now_ms()
    errors = list(state.get("errors") or [])
    try:
        ranked = await runner.hypothesis_ranker_agent.rank_hypotheses(
            state["move"],
            state.get("plan") or {},
            state.get("evidence_items") or [],
        )
    except Exception as exc:
        errors.append(f"rank: {exc}")
        seeds = state.get("hypotheses") or []
        ranked = {
            "summary": "Hypothesis ranking failed; using planner seeds.",
            "hypotheses": [
                {
                    "statement": seed,
                    "stance": "unknown",
                    "confidence_score": 0.35,
                    "weight": 1.0 / max(1, len(seeds)),
                    "devil_advocate_notes": "",
                    "evidence_indices": [0] if state.get("evidence_items") else [],
                    "rank": i,
                }
                for i, seed in enumerate(seeds[:5], start=1)
            ],
        }
    metrics = dict(state.get("metrics") or {})
    metrics["rank_ms"] = _now_ms() - start
    hyps = ranked.get("hypotheses") or []
    engine_payload = state.get("engine_result") or ranked.get("engine") or {}
    if engine_payload:
        from app.models.hypothesis_schemas import HypothesisEngineResult
        from app.services import hypothesis_engine

        try:
            engine = HypothesisEngineResult.model_validate(engine_payload)
            hyps = hypothesis_engine.overlay_engine_fields(hyps, engine)
            ranked = dict(ranked)
            ranked["hypotheses"] = hyps
            ranked.setdefault("engine", engine_payload)
            ranked.setdefault("leading_hypothesis_id", engine.leading_hypothesis_id)
            ranked.setdefault(
                "strongest_alternative_id", engine.strongest_alternative_id
            )
        except Exception:
            pass
    selected = hyps[0]["statement"] if hyps else ""
    return {
        **state,
        "ranked": ranked,
        "hypothesis_rows": hyps,
        "selected_hypothesis": selected,
        "status": "verifying",
        "metrics": metrics,
        "errors": errors,
        "engine_result": engine_payload or state.get("engine_result") or {},
    }


async def verify_node(state: InvestigationState) -> InvestigationState:
    from app.services import investigation_runner as runner

    start = _now_ms()
    _log("VERIFICATION_STARTED", state, graph_node="verify")
    errors = list(state.get("errors") or [])
    try:
        verified = await runner.investigation_verification_agent.verify_hypotheses(
            state["ticker"],
            (state.get("ranked") or {}).get("hypotheses") or [],
            state.get("evidence_items") or [],
            hypothesis_context=_hypothesis_context(state),
            investigation_id=int(state.get("investigation_id") or 0),
            event_time=_event_time(state),
        )
    except Exception as exc:
        errors.append(f"verify: {exc}")
        hyps = (state.get("ranked") or {}).get("hypotheses") or state.get("hypothesis_rows") or []
        verified = {
            "hypotheses": [h for h in hyps if h.get("evidence_indices")],
            "rejected": [],
            "notes": ["Verification agent failed; kept only already-cited hypotheses."],
            "citation_coverage": 0.0,
        }
    metrics = dict(state.get("metrics") or {})
    metrics["verify_ms"] = _now_ms() - start
    return {
        **state,
        "verified": verified,
        "hypothesis_rows": verified.get("hypotheses") or state.get("hypothesis_rows") or [],
        "metrics": metrics,
        "status": "verifying",
        "errors": errors,
    }


async def challenge_node(state: InvestigationState) -> InvestigationState:
    from app.services import investigation_runner as runner
    from app.services import verification_engine

    start = _now_ms()
    _log("DEVILS_ADVOCATE_STARTED", state, graph_node="challenge")
    errors = list(state.get("errors") or [])
    incoming = (
        (state.get("verified") or {}).get("hypotheses")
        or state.get("hypothesis_rows")
        or []
    )
    ctx = _hypothesis_context(state)
    ctx["verification_report"] = (state.get("verified") or {}).get("verification_report") or {}
    try:
        da = await runner.devils_advocate_agent.stress_test_leading(
            state["move"],
            incoming,
            state.get("evidence_items") or [],
            hypothesis_context=ctx,
        )
    except Exception as exc:
        errors.append(f"devils_advocate: {exc}")
        da = {
            "hypotheses": incoming,
            "outcome": "held",
            "counterargument": "Devil's Advocate failed; leading hypothesis was not challenged.",
            "leading_weakened": False,
            "reversal": False,
            "notes": [str(exc)],
            "confidence_delta": 0.0,
            "challenge_severity": "LOW",
            "recommendation": "ACCEPT",
        }
    structured = _structured_da(da)
    metrics = dict(state.get("metrics") or {})
    metrics["da_ms"] = _now_ms() - start
    explicit_challenge = bool(
        da.get("challenge_severity")
        or da.get("recommendation")
        or da.get("devils_advocate_result")
    )
    report = (state.get("verified") or {}).get("verification_report") or {}
    apply_reasons = explicit_challenge or bool(report.get("claim_checks"))
    reasons = list(report.get("research_more_reasons") or [])
    needs_text = list(structured.additional_evidence_needed or []) + (
        reasons if apply_reasons else []
    )
    existing_tasks = [t.get("required_tool") for t in (state.get("tasks") or []) if t.get("required_tool")]
    unused = verification_engine.unused_challenge_tools(
        needs_text,
        existing_tools=existing_tasks,
        evidence=state.get("evidence_items") or [],
    )
    loops = int(state.get("challenge_loops") or 0)
    max_loops = int(metrics.get("max_challenge_loops") or MAX_CHALLENGE_LOOPS)
    max_iter = int(metrics.get("max_iterations") or MAX_INVESTIGATION_ITERATIONS)
    needs = verification_engine.should_request_challenge_research(
        structured,
        unused_tools=unused,
        challenge_loops=loops,
        max_loops=max_loops,
        iteration_count=int(state.get("iteration_count") or 0),
        max_iterations=max_iter,
        research_reasons=reasons if apply_reasons else [],
        apply_reasons=apply_reasons,
    )
    assessment = dict(state.get("assessment") or {})
    _log(
        "DEVILS_ADVOCATE_COMPLETED",
        state,
        graph_node="challenge",
        challenge_severity=structured.challenge_severity,
        recommendation=structured.recommendation,
        hypothesis_id=structured.target_hypothesis_id,
        verification_status=structured.recommendation,
    )
    if needs:
        assessment["recommended_next_tools"] = unused
        assessment["sufficient_evidence"] = False
        loops += 1
        _log(
            "ADDITIONAL_RESEARCH_REQUIRED",
            state,
            graph_node="challenge",
            recommended=unused,
            challenge_severity=structured.challenge_severity,
            recommendation=structured.recommendation,
        )
        _log(
            "MORE_RESEARCH_REQUIRED",
            state,
            graph_node="challenge",
            recommended=unused,
            challenge_severity=structured.challenge_severity,
            verification_status=structured.recommendation,
        )
    da = dict(da)
    da.update(structured.model_dump())
    return {
        **state,
        "da": da,
        "hypothesis_rows": da.get("hypotheses")
        or (state.get("verified") or {}).get("hypotheses")
        or [],
        "metrics": metrics,
        "assessment": assessment,
        "needs_challenge_research": needs,
        "challenge_loops": loops,
        "sufficient_evidence": False if needs else state.get("sufficient_evidence"),
        "status": "verifying",
        "errors": errors,
    }


async def synthesize_node(state: InvestigationState) -> InvestigationState:
    from app.models.verification_schemas import VerificationReport
    from app.services import verification_engine

    hyps = list(state.get("hypothesis_rows") or [])
    evidence = list(state.get("evidence_items") or [])
    raw_report = (state.get("verified") or {}).get("verification_report") or {}
    report = None
    if raw_report:
        try:
            report = VerificationReport.model_validate(raw_report)
        except Exception:
            report = None
    da = state.get("da") or {}
    move = state.get("move")
    ticker = str(state.get("ticker") or getattr(move, "ticker", "") or "Ticker")
    pct = getattr(move, "move_pct", None)
    window = str(getattr(move, "window_label", None) or "1d")
    if pct is None:
        move_line = f"{ticker} move over {window} could not be measured cleanly."
    else:
        abs_move = abs(float(pct))
        verb = "fell" if float(pct) < 0 else "rose"
        move_line = f"{ticker} {verb} {abs_move:.1f}% over {window}."
    final_report = verification_engine.build_final_report(
        move_line=move_line,
        hypotheses=hyps,
        evidence=evidence,
        verification=report,
        da=da,
        portfolio_exposure=float(state.get("portfolio_exposure") or 0),
    )
    if final_report.conclusion_strength in {"INSUFFICIENT_EVIDENCE", "CONTRADICTED", "UNCERTAIN"}:
        if hyps and final_report.conclusion_strength == "INSUFFICIENT_EVIDENCE":
            hyps = [dict(hyps[0])] + list(hyps[1:])
            hyps[0]["statement"] = final_report.why_it_likely_happened
        _log(
            "CONCLUSION_DOWNGRADED",
            state,
            graph_node="synthesize",
            verification_status=final_report.conclusion_strength,
            challenge_severity=str(da.get("challenge_severity") or ""),
            hypothesis_id=str((hyps[0] if hyps else {}).get("hypothesis_id") or ""),
        )
    else:
        _log(
            "CONCLUSION_ACCEPTED",
            state,
            graph_node="synthesize",
            verification_status=final_report.conclusion_strength,
            challenge_severity=str(da.get("challenge_severity") or ""),
            hypothesis_id=str((hyps[0] if hyps else {}).get("hypothesis_id") or ""),
        )
    _log(
        "INVESTIGATION_SYNTHESIS",
        state,
        graph_node="synthesize",
        iterations=state.get("iteration_count"),
        incomplete=state.get("incomplete_evidence"),
        leading=(state.get("engine_result") or {}).get("leading_hypothesis_id"),
        challenge_severity=da.get("challenge_severity"),
        recommendation=da.get("recommendation"),
        verification_status=final_report.conclusion_strength,
    )
    return {
        **state,
        "hypothesis_rows": hyps or state.get("hypothesis_rows") or [],
        "final_report": final_report.model_dump(),
        "status": "complete",
    }


def _hypothesis_context(state: InvestigationState) -> Dict[str, Any]:
    from app.models.hypothesis_schemas import HypothesisEngineResult
    from app.services import evidence_graph, hypothesis_engine

    raw = state.get("engine_result") or (state.get("ranked") or {}).get("engine") or {}
    ctx: Dict[str, Any] = {}
    if raw:
        try:
            engine = HypothesisEngineResult.model_validate(raw)
            ctx.update(
                hypothesis_engine.context_for_leading(
                    engine, state.get("evidence_items") or []
                )
            )
        except Exception:
            pass
    hyps = (
        (state.get("ranked") or {}).get("hypotheses")
        or state.get("hypothesis_rows")
        or []
    )
    snapshot = evidence_graph.build_snapshot(
        hyps,
        state.get("evidence_items") or [],
        investigation_id=int(state.get("investigation_id") or 0),
    )
    ctx.update(evidence_graph.advocate_context(snapshot))
    ctx["claim_assessments"] = snapshot.assessments
    event = state.get("event") or {}
    ctx["event"] = event
    move = state.get("move")
    ctx["market_context"] = {
        "move_pct": getattr(move, "move_pct", None),
        "residual_pct": getattr(move, "residual_pct", None),
        "window_label": getattr(move, "window_label", None),
        "benchmark": getattr(move, "benchmark", None),
    }
    ctx["temporal_information"] = {
        "occurred_at": event.get("occurred_at") or event.get("detected_at"),
    }
    memory = state.get("company_memory") or {}
    if memory:
        from app.services.company_memory import historical_vs_current_note

        ctx["company_memory"] = memory
        ctx["historical_memory_note"] = historical_vs_current_note()
    return ctx


def _event_time(state: InvestigationState):
    from app.services import hypothesis_engine

    event = state.get("event") or {}
    return hypothesis_engine._parse_time(event.get("occurred_at") or event.get("detected_at"))


def _structured_da(da: Dict[str, Any]):
    from app.models.verification_schemas import DevilsAdvocateResult
    from app.services import verification_engine

    raw = da.get("devils_advocate_result") if isinstance(da.get("devils_advocate_result"), dict) else da
    try:
        result = DevilsAdvocateResult.model_validate(raw)
    except Exception:
        result = DevilsAdvocateResult(
            strongest_counterargument=str(da.get("counterargument") or ""),
            additional_evidence_needed=verification_engine._as_list(
                da.get("additional_evidence_needed")
            ),
            challenge_severity=da.get("challenge_severity") or "LOW",
            recommendation=da.get("recommendation") or "ACCEPT",
        )
    # Mocked DA without severity/recommendation must not invent a research loop.
    if not da.get("challenge_severity") and not da.get("recommendation") and not da.get(
        "devils_advocate_result"
    ):
        result.challenge_severity = "LOW"
        result.recommendation = "ACCEPT"
        if not da.get("additional_evidence_needed"):
            result.additional_evidence_needed = []
    return result


def route_after_assess(state: InvestigationState) -> str:
    if state.get("sufficient_evidence"):
        return "rank"
    return "identify_gaps"


def route_after_challenge(state: InvestigationState) -> str:
    if state.get("needs_challenge_research"):
        return "identify_gaps"
    return "synthesize"


def _build_graph():
    workflow = StateGraph(InvestigationState)
    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("planner", planner_node)
    workflow.add_node("create_tasks", create_tasks_node)
    workflow.add_node("research", research_node)
    workflow.add_node("assess", assess_node)
    workflow.add_node("identify_gaps", identify_gaps_node)
    workflow.add_node("rank", rank_node)
    workflow.add_node("verify", verify_node)
    workflow.add_node("challenge", challenge_node)
    workflow.add_node("synthesize", synthesize_node)

    workflow.set_entry_point("load_memory")
    workflow.add_edge("load_memory", "planner")
    workflow.add_edge("planner", "create_tasks")
    workflow.add_edge("create_tasks", "research")
    workflow.add_edge("research", "assess")
    workflow.add_conditional_edges(
        "assess",
        route_after_assess,
        {"identify_gaps": "identify_gaps", "rank": "rank"},
    )
    workflow.add_edge("identify_gaps", "research")
    workflow.add_edge("rank", "verify")
    workflow.add_edge("verify", "challenge")
    workflow.add_conditional_edges(
        "challenge",
        route_after_challenge,
        {"identify_gaps": "identify_gaps", "synthesize": "synthesize"},
    )
    workflow.add_edge("synthesize", END)
    return workflow.compile()


_graph = None


def get_investigation_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def reset_investigation_graph() -> None:
    global _graph
    _graph = None


async def run_investigation_graph(
    move: MoveSnapshot,
    *,
    ticker: str,
    investigation_id: int = 0,
    asset_class: str = "equity",
    event: Optional[Dict[str, Any]] = None,
    materiality_score: float = 0.0,
    portfolio_exposure: float = 0.0,
    max_iterations: Optional[int] = None,
    owner_key: str = "",
    company_memory: Optional[Dict[str, Any]] = None,
) -> InvestigationGraphResult:
    graph = get_investigation_graph()
    limit = max(1, min(4, max_iterations or MAX_INVESTIGATION_ITERATIONS))
    initial: InvestigationState = {
        "investigation_id": investigation_id,
        "ticker": ticker,
        "event": event or {},
        "event_type": str((event or {}).get("event_type") or "PRICE_MOVE"),
        "investigation_depth": getattr(move, "depth", None) or "standard",
        "materiality_score": materiality_score,
        "portfolio_exposure": portfolio_exposure,
        "asset_class": asset_class,
        "move": move,
        "plan": {},
        "research_plan": {},
        "hypotheses": [],
        "hypothesis_rows": [],
        "evidence_items": [],
        "tasks": [],
        "assessment": {},
        "ranked": {},
        "verified": {},
        "da": {},
        "status": "planning",
        "iteration_count": 0,
        "errors": [],
        "metrics": {
            "max_iterations": limit,
            "max_challenge_loops": max(0, min(2, MAX_CHALLENGE_LOOPS)),
        },
        "incomplete_evidence": False,
        "sufficient_evidence": False,
        "hypothesis_records": [],
        "engine_result": {},
        "challenge_loops": 0,
        "needs_challenge_research": False,
        "final_report": {},
        "owner_key": owner_key,
        "company_memory": company_memory or {},
    }
    final = await graph.ainvoke(initial)
    hyps = list(final.get("hypothesis_rows") or [])
    hyps = [h for h in hyps if h.get("evidence_indices")] or hyps
    _log(
        "INVESTIGATION_COMPLETED",
        final,
        graph_node="end",
        status=final.get("status"),
        evidence=len(final.get("evidence_items") or []),
        claims=len(hyps),
    )
    return InvestigationGraphResult(
        plan=final.get("plan") or {},
        research_plan=final.get("research_plan") or {},
        evidence_rows=final.get("evidence_items") or [],
        hypotheses=hyps,
        ranked=final.get("ranked") or {},
        verified=final.get("verified") or {},
        da=final.get("da") or {},
        assessment=final.get("assessment") or {},
        iteration_count=int(final.get("iteration_count") or 0),
        challenge_loops=int(final.get("challenge_loops") or 0),
        status=str(final.get("status") or "complete"),
        errors=list(final.get("errors") or []),
        metrics=dict(final.get("metrics") or {}),
        incomplete_evidence=bool(final.get("incomplete_evidence")),
        engine=final.get("engine_result") or {},
        verification_report=(final.get("verified") or {}).get("verification_report") or {},
        final_report=final.get("final_report") or {},
        conclusion_strength=str((final.get("final_report") or {}).get("conclusion_strength") or ""),
        evidence_gaps=list((final.get("final_report") or {}).get("evidence_gaps") or []),
        company_memory=final.get("company_memory") or {},
    )

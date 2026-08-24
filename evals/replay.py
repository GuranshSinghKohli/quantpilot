"""Replay a case through production component functions.

Ground truth is never passed into these calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from evals._path import ensure_backend_on_path
from evals.schemas import EvidenceFixture, PackedCase, ReplayFixture
from evals.temporal import filter_evidence, parse_time


def _move_snapshot(fx: ReplayFixture, ticker: str):
    ensure_backend_on_path()
    from app.services.move_detector import MoveSnapshot

    m = fx.move
    if m is None:
        return MoveSnapshot(
            ticker=ticker,
            move_pct=None,
            window_label="1d",
            current_price=None,
            previous_close=None,
            is_noise=True,
            source="eval_replay",
        )
    return MoveSnapshot(
        ticker=m.ticker,
        move_pct=m.move_pct,
        window_label=m.window_label,
        current_price=m.current_price,
        previous_close=m.previous_close,
        is_noise=False,
        source=m.source,
        realized_vol_pct=m.realized_vol_pct,
        move_zscore=m.move_zscore,
        benchmark_move_pct=m.benchmark_move_pct,
        residual_pct=m.residual_pct,
    )


def _trigger(fx: ReplayFixture, ticker: str):
    ensure_backend_on_path()
    from app.services.trigger_logic import _decide

    move = _move_snapshot(fx, ticker)
    m = fx.move
    return _decide(
        move=move,
        asset_class="equity",
        realized_vol_pct=m.realized_vol_pct if m else None,
        move_zscore=m.move_zscore if m else None,
        benchmark_move_pct=m.benchmark_move_pct if m else None,
        residual_pct=m.residual_pct if m else None,
        benchmark="SPY",
    )


def _evidence_dicts(items: List[EvidenceFixture]) -> List[Dict[str, Any]]:
    rows = []
    for item in items:
        rows.append(
            {
                "title": item.title,
                "excerpt": item.excerpt,
                "source_url": item.source_url,
                "source_type": item.source_type,
                "source_name": item.source_name,
                "published_at": item.published_at,
                "retrieved_at": item.retrieved_at,
                "created_at": item.retrieved_at,
                "raw_payload": {
                    "published_at": item.published_at,
                    "provenance": {
                        "published_at": item.published_at,
                        "retrieved_at": item.retrieved_at,
                    },
                },
            }
        )
    return rows


def _hyp_dicts(packed: PackedCase, evidence_n: int) -> List[Dict[str, Any]]:
    rows = []
    for i, h in enumerate(packed.fixture.hypotheses):
        idxs = [j for j in h.evidence_indices if 0 <= j < evidence_n]
        rows.append(
            {
                "hypothesis_id": f"h{i+1}",
                "statement": h.statement,
                "rank": h.rank,
                "claim_type": h.claim_type,
                "evidence_indices": idxs,
                "contradicting_indices": [
                    j for j in h.contradicting_indices if 0 <= j < evidence_n
                ],
                "confidence_score": {"HIGH": 0.8, "MEDIUM": 0.55, "LOW": 0.35}.get(
                    h.confidence, 0.5
                ),
            }
        )
    return rows


def detect_events(packed: PackedCase) -> List[Any]:
    ensure_backend_on_path()
    from app.services.event_detection import (
        events_from_filings,
        events_from_fundamentals,
        events_from_trigger,
    )

    now = parse_time(packed.case.event_timestamp)
    decision = _trigger(packed.fixture, packed.case.ticker)
    events = list(events_from_trigger(decision))
    if packed.fixture.filings:
        payload = {
            "filings": [
                {
                    "form_type": f.form_type,
                    "filing_date": f.filing_date,
                    "document_url": f.document_url,
                    "accession_number": f.accession_number,
                }
                for f in packed.fixture.filings
            ]
        }
        events.extend(
            events_from_filings(packed.case.ticker, payload, now=now, lookback_days=14)
        )
    if packed.fixture.fundamentals:
        events.extend(
            events_from_fundamentals(
                packed.case.ticker,
                packed.fixture.fundamentals,
                now=now,
                lookback_days=7,
                lookahead_days=7,
            )
        )
    return events


def replay_case(
    packed: PackedCase,
    *,
    ablation: str = "full",
    skip_memory: bool = False,
) -> Dict[str, Any]:
    """Run production components. `truth` is unused here by design."""
    ensure_backend_on_path()
    from app.services.autonomous_monitor import decide_notify
    from app.services.materiality import (
        assess_investigation_materiality,
        assess_materiality,
    )
    from app.services import materiality as materiality_mod
    from app.services.verification_engine import (
        build_devils_advocate_result,
        build_final_report,
        verify_investigation,
    )

    cutoff = parse_time(packed.case.cutoff_time)
    allow_retro = packed.case.allow_retrospective
    raw_evidence = list(packed.fixture.evidence)
    if ablation == "no_browser":
        raw_evidence = [e for e in raw_evidence if e.source_type != "ir_page"]
    evidence = filter_evidence(raw_evidence, cutoff, allow_retrospective=allow_retro)

    events = detect_events(packed)
    primary = materiality_mod.pick_primary_event(events)
    exposure = packed.fixture.exposure
    if primary is not None:
        primary.portfolio_exposure = exposure
        mat = assess_investigation_materiality(primary, exposure=exposure)
    else:
        mat = None

    move = _move_snapshot(packed.fixture, packed.case.ticker)
    notify_mat = assess_materiality(move)

    ev_dicts = _evidence_dicts(evidence)
    hyps = _hyp_dicts(packed, len(ev_dicts))
    if ablation == "baseline":
        from evals.baseline import run_baseline

        hyps = [
            {
                "hypothesis_id": "b1",
                "statement": h.statement,
                "rank": h.rank,
                "claim_type": h.claim_type,
                "evidence_indices": h.evidence_indices,
                "contradicting_indices": [],
                "confidence_score": 0.9,
            }
            for h in run_baseline(packed)
        ]
        # Baseline does not honor cutoff.
        ev_dicts = _evidence_dicts(raw_evidence)

    vrep = None
    rewritten = hyps
    if ablation != "no_verification" and hyps:
        rewritten, vrep = verify_investigation(
            hyps, ev_dicts, event_time=cutoff
        )

    da = None
    if ablation not in {"no_da", "no_verification", "baseline"}:
        da = build_devils_advocate_result(
            rewritten or hyps,
            ev_dicts,
            verification_report=vrep,
        )

    report = None
    if ablation != "no_verification":
        report = build_final_report(
            move_line=f"{packed.case.ticker} {packed.case.event_description}",
            hypotheses=rewritten or hyps,
            evidence=ev_dicts,
            verification=vrep,
            da=da.model_dump() if da is not None else {},
            portfolio_exposure=exposure,
        )

    notify = decide_notify(
        status=packed.fixture.investigation_status,
        trigger_reason=packed.fixture.trigger_reason,
        claims=rewritten or hyps,
        move=move,
        notify_enabled=True,
        user_present=True,
        recent_notification=False,
    )

    memory_out: Dict[str, Any] = {}
    if not skip_memory and ablation != "no_memory" and packed.fixture.memory:
        memory_out = _retrieve_memory(packed)

    used_browser = any(
        "browser" in t.tool for t in packed.fixture.tool_trace
    ) or bool(packed.fixture.browser_found_url)
    if ablation == "no_browser":
        used_browser = False

    hyp_models = packed.fixture.hypotheses
    if ablation == "baseline":
        from evals.baseline import run_baseline

        hyp_models = run_baseline(packed)

    return {
        "events": [e.event_type.value for e in events],
        "hypothesis_models": hyp_models,
        "should_investigate_trigger": _trigger(packed.fixture, packed.case.ticker).should_investigate,
        "should_investigate_materiality": bool(mat.should_investigate) if mat else False,
        "materiality_score": float(mat.score) if mat else 0.0,
        "materiality_reason": mat.reason if mat else "no event",
        "primary_event": primary.event_type.value if primary else "",
        "evidence": evidence,
        "hypotheses": [str(h.get("statement") or "") for h in (rewritten or hyps)],
        "claim_statuses": [
            str(c.support_status)
            for c in (vrep.claim_checks if vrep else [])
        ],
        "verification_issues": _collect_issues(vrep),
        "flagged_unsupported": bool(
            vrep and any(c.support_status == "UNSUPPORTED" for c in vrep.claim_checks)
        )
        if vrep
        else False,
        "flagged_temporal": bool(
            vrep and any(c.retrospective for c in vrep.claim_checks)
        )
        if vrep
        else False,
        "conclusion_strength": str(report.conclusion_strength) if report else "",
        "conclusion": (report.why_it_likely_happened if report else ""),
        "da_counter": da.strongest_counterargument if da else "",
        "da_alternative": da.alternative_hypothesis_id if da else "",
        "da_needed": list(da.additional_evidence_needed) if da else [],
        "da_challenged_causal": bool(
            da and "causal" in " ".join(da.questionable_assumptions).lower()
        )
        if da
        else False,
        "should_notify": bool(notify.should_notify),
        "notify_reason": notify.reason,
        "notify_score": notify_mat.score,
        "memory": memory_out,
        "used_browser": used_browser,
        "browser_domain": (
            urlparse(packed.fixture.browser_found_url).hostname or ""
        ).lower(),
        "tool_trace": packed.fixture.tool_trace,
        "latency_ms": dict(packed.fixture.latency_ms),
        "llm_calls": packed.fixture.llm_calls,
        "llm_tokens": packed.fixture.llm_tokens,
    }


def _collect_issues(vrep: Any) -> List[str]:
    if vrep is None:
        return []
    out: List[str] = []
    for c in vrep.source_checks:
        out.extend(c.issues)
    for c in vrep.claim_checks:
        out.extend(c.issues)
        out.extend(c.contradictions)
    out.extend(vrep.notes)
    if vrep.hypothesis_check:
        out.extend(vrep.hypothesis_check.gaps)
        out.extend(vrep.hypothesis_check.issues)
    return [str(x) for x in out]


def _retrieve_memory(packed: PackedCase) -> Dict[str, Any]:
    from datetime import timezone

    from evals.isolation import isolated_db

    ensure_backend_on_path()
    from app.models.investigation_schemas import InvestigationCreateRequest
    from app.services import company_memory, evidence_ledger_store

    owner = "eval:phase11"
    with isolated_db() as db:
        case = evidence_ledger_store.create_investigation(
            db,
            owner_key=owner,
            user=None,
            body=InvestigationCreateRequest(
                ticker=packed.case.ticker,
                trigger_reason="scheduled",
                summary="eval memory seed",
            ),
        )
        for item in packed.fixture.memory:
            company_memory.upsert_memory(
                db,
                owner_key=owner,
                candidate={
                    "ticker": packed.case.ticker,
                    "memory_type": item.memory_type,
                    "statement": item.statement,
                    "theme_key": item.theme_key,
                    "polarity": item.polarity,
                    "observed_at": parse_time(item.observed_at),
                    "investigation_id": case.id,
                    "source_url": item.source_url,
                    "source_type": item.source_type,
                },
            )
        ctx = company_memory.retrieve_for_investigation(
            db, owner_key=owner, ticker=packed.case.ticker
        )
        trend = company_memory.detect_management_trend(
            company_memory._load_active(db, owner, packed.case.ticker, include_outdated=True)
        )
        return {
            "statements": [i.statement for i in ctx.items],
            "trend": trend or ctx.management_trend,
            "conflicts": list(ctx.conflicts),
        }

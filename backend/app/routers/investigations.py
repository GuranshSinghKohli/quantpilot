"""PRD v3 Phase 7 — Evidence Ledger API."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_optional_user
from app.db.models import User
from app.db.session import get_db
from app.jobs.investigation_sweep_job import run_investigation_sweep
from app.models.investigation_schemas import (
    ClaimCreateRequest,
    ClaimEvidenceLinkOut,
    ClaimEvidenceLinkRequest,
    ClaimOut,
    EvidenceCreateRequest,
    EvidenceItemOut,
    InvestigateTickerRequest,
    InvestigationCreateRequest,
    InvestigationDetail,
    InvestigationRunRequest,
    InvestigationSearchResponse,
    InvestigationSummary,
    InvestigationSweepRequest,
    InvestigationSweepResponse,
    InvestigationChatRequest,
    SmartSummarizeTextRequest,
    SmartSummaryResponse,
    TriggerPreviewResponse,
)
from app.models.agent_schemas import ChatResponse
from app.services import (
    evidence_ledger_store,
    investigation_chat,
    investigation_runner,
    investigation_search,
    investigation_smart_summary,
    trigger_logic,
)

router = APIRouter(tags=["investigations"])


def _require_owner(
    user: Optional[User],
    x_guest_id: Optional[str],
) -> str:
    owner = evidence_ledger_store.resolve_owner(user, x_guest_id)
    if not owner:
        raise HTTPException(
            status_code=400,
            detail="Provide auth or X-Guest-Id to scope investigations.",
        )
    return owner


@router.post("/investigations", response_model=InvestigationDetail, status_code=201)
async def create_investigation(
    body: InvestigationCreateRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationDetail:
    owner_key = _require_owner(user, x_guest_id)
    detail = evidence_ledger_store.create_investigation(
        db, owner_key=owner_key, user=user, body=body
    )
    if detail is None:
        raise HTTPException(status_code=500, detail="Failed to create investigation")
    return detail


@router.post(
    "/investigations/investigate",
    response_model=InvestigationDetail,
    status_code=201,
)
async def investigate_ticker(
    body: InvestigateTickerRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationDetail:
    """PRD v3 Phase 8 — create + run reactive why-did-it-move pipeline."""
    owner_key = _require_owner(user, x_guest_id)
    try:
        return await investigation_runner.investigate_ticker(
            db,
            owner_key=owner_key,
            user=user,
            ticker=body.ticker,
            trigger_reason=body.trigger_reason,
            window_label=body.window_label,
            skip_if_noise=body.skip_if_noise,
            use_trigger_gate=body.use_trigger_gate,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Investigation failed: {exc}",
        ) from exc


@router.post(
    "/investigations/sweep",
    response_model=InvestigationSweepResponse,
)
async def sweep_investigations(
    body: InvestigationSweepRequest = InvestigationSweepRequest(),
    user: User = Depends(get_current_user),
) -> InvestigationSweepResponse:
    """PRD v3 Phase 11 — scan this user's holdings with trigger logic."""
    try:
        result = await run_investigation_sweep(
            user=user,
            max_launches=body.max_launches,
            dry_run=body.dry_run,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Investigation sweep failed: {exc}",
        ) from exc
    return InvestigationSweepResponse.model_validate(result)


@router.get(
    "/investigations/trigger/{ticker}",
    response_model=TriggerPreviewResponse,
)
async def preview_trigger(
    ticker: str,
    window_label: str = "1d",
    _user: Optional[User] = Depends(get_optional_user),
) -> TriggerPreviewResponse:
    """Preview Phase 11 idiosyncratic / vol-adjusted trigger decision."""
    try:
        decision = await trigger_logic.evaluate_trigger(
            ticker, window=window_label or "1d"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Trigger evaluation failed: {exc}",
        ) from exc
    return TriggerPreviewResponse(
        ticker=decision.move.ticker,
        should_investigate=decision.should_investigate,
        reason=decision.reason,
        depth=decision.depth,
        asset_class=decision.asset_class,
        move_pct=decision.move.move_pct,
        realized_vol_pct=decision.realized_vol_pct,
        move_zscore=decision.move_zscore,
        benchmark_move_pct=decision.benchmark_move_pct,
        residual_pct=decision.residual_pct,
        window_label=decision.move.window_label,
    )


@router.get("/investigations", response_model=List[InvestigationSummary])
async def list_investigations(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> List[InvestigationSummary]:
    owner_key = evidence_ledger_store.resolve_owner(user, x_guest_id)
    if not owner_key:
        return []
    return evidence_ledger_store.list_investigations(db, owner_key, limit=limit)


@router.get(
    "/investigations/search",
    response_model=InvestigationSearchResponse,
)
async def search_investigations(
    q: str = Query(..., min_length=1, max_length=400, description="Natural language query"),
    limit: int = Query(10, ge=1, le=25),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationSearchResponse:
    """PRD v3 FR-10 — search prior investigations, claims, and evidence."""
    owner_key = evidence_ledger_store.resolve_owner(user, x_guest_id)
    if not owner_key:
        return InvestigationSearchResponse(query=q, mode="empty", results=[])
    try:
        payload = investigation_search.search_investigations(
            db, owner_key=owner_key, query=q, limit=limit
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Investigation search failed: {exc}",
        ) from exc
    return InvestigationSearchResponse.model_validate(payload)


@router.get("/investigations/{investigation_id}", response_model=InvestigationDetail)
async def get_investigation(
    investigation_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationDetail:
    owner_key = _require_owner(user, x_guest_id)
    detail = evidence_ledger_store.get_investigation(
        db, owner_key=owner_key, investigation_id=investigation_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return detail


@router.post(
    "/investigations/{investigation_id}/evidence",
    response_model=EvidenceItemOut,
    status_code=201,
)
async def add_evidence(
    investigation_id: int,
    body: EvidenceCreateRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> EvidenceItemOut:
    owner_key = _require_owner(user, x_guest_id)
    item = evidence_ledger_store.add_evidence(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        body=body,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return item


@router.post(
    "/investigations/{investigation_id}/claims",
    response_model=ClaimOut,
    status_code=201,
)
async def add_claim(
    investigation_id: int,
    body: ClaimCreateRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> ClaimOut:
    owner_key = _require_owner(user, x_guest_id)
    claim = evidence_ledger_store.add_claim(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        body=body,
    )
    if claim is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return claim


@router.post(
    "/investigations/{investigation_id}/links",
    response_model=ClaimEvidenceLinkOut,
    status_code=201,
)
async def link_claim_evidence(
    investigation_id: int,
    body: ClaimEvidenceLinkRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> ClaimEvidenceLinkOut:
    owner_key = _require_owner(user, x_guest_id)
    link = evidence_ledger_store.link_claim_evidence(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        claim_id=body.claim_id,
        evidence_id=body.evidence_id,
        relation=body.relation,
        note=body.note,
    )
    if link is None:
        raise HTTPException(
            status_code=404,
            detail="Investigation, claim, or evidence not found",
        )
    return link


@router.post(
    "/investigations/{investigation_id}/smart-summarize",
    response_model=SmartSummaryResponse,
)
async def smart_summarize_investigation(
    investigation_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> SmartSummaryResponse:
    """Produce a short wrap-up: move, cause, and agent takeaways."""
    owner_key = _require_owner(user, x_guest_id)
    detail = evidence_ledger_store.get_investigation(
        db, owner_key=owner_key, investigation_id=investigation_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return await investigation_smart_summary.smart_summarize_investigation(detail)


@router.post("/smart-summarize", response_model=SmartSummaryResponse)
async def smart_summarize_text(
    body: SmartSummarizeTextRequest,
) -> SmartSummaryResponse:
    """Smart summarize arbitrary report text (deep research reports)."""
    return await investigation_smart_summary.smart_summarize_text(
        title=body.title, body=body.body
    )


@router.post(
    "/investigations/{investigation_id}/chat",
    response_model=ChatResponse,
)
async def investigation_rag_chat(
    investigation_id: int,
    body: InvestigationChatRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> ChatResponse:
    """RAG Q&A over the open case (claims, evidence) + related ledger hits."""
    owner_key = _require_owner(user, x_guest_id)
    detail = evidence_ledger_store.get_investigation(
        db, owner_key=owner_key, investigation_id=investigation_id
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return await investigation_chat.answer_investigation_question(
        db,
        owner_key=owner_key,
        detail=detail,
        question=body.question.strip(),
    )


@router.post(
    "/investigations/{investigation_id}/complete",
    response_model=InvestigationDetail,
)
async def complete_investigation(
    investigation_id: int,
    summary: str = "",
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationDetail:
    owner_key = _require_owner(user, x_guest_id)
    detail = evidence_ledger_store.mark_complete(
        db,
        owner_key=owner_key,
        investigation_id=investigation_id,
        summary=summary,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return detail


@router.post(
    "/investigations/{investigation_id}/run",
    response_model=InvestigationDetail,
)
async def run_investigation(
    investigation_id: int,
    body: Optional[InvestigationRunRequest] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    x_guest_id: Optional[str] = Header(default=None, alias="X-Guest-Id"),
) -> InvestigationDetail:
    """PRD v3 Phase 8 — run planner → collect → rank on an existing case."""
    owner_key = _require_owner(user, x_guest_id)
    existing = evidence_ledger_store.get_investigation(
        db, owner_key=owner_key, investigation_id=investigation_id
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    req = body or InvestigationRunRequest()
    try:
        return await investigation_runner.run_investigation(
            db,
            owner_key=owner_key,
            user=user,
            investigation_id=investigation_id,
            skip_if_noise=req.skip_if_noise,
            window_label=req.window_label or existing.window_label or "1d",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Investigation run failed: {exc}",
        ) from exc

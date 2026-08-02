"""PRD v3 Phase 7 — Evidence Ledger request/response schemas."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class EvidenceItemOut(BaseModel):
    id: int
    source_type: str
    retrieval_method: str
    title: str
    excerpt: str
    source_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ClaimEvidenceLinkOut(BaseModel):
    id: int
    evidence_id: int
    relation: str
    note: str
    evidence: Optional[EvidenceItemOut] = None

    model_config = {"from_attributes": True}


class ClaimOut(BaseModel):
    id: int
    statement: str
    stance: str
    confidence_score: float
    rank: int
    devil_advocate_notes: str
    evidence_links: List[ClaimEvidenceLinkOut] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class InvestigationSummary(BaseModel):
    id: int
    ticker: str
    trigger_reason: str
    status: str
    move_pct: Optional[float] = None
    window_label: str = ""
    summary: str = ""
    created_at: datetime
    completed_at: Optional[datetime] = None
    claims_count: int = 0
    evidence_count: int = 0

    model_config = {"from_attributes": True}


class DevilsAdvocateOutcome(BaseModel):
    outcome: str = "held"
    counterargument: str = ""
    leading_weakened: bool = False
    reversal: bool = False
    notes: List[str] = Field(default_factory=list)
    confidence_delta: float = 0.0
    citation_coverage: float = 0.0


class InvestigationRosterContext(BaseModel):
    """PRD v3 Phase 13 — light earnings / macro / memo attached to a case."""

    earnings: Dict[str, Any] = Field(default_factory=dict)
    macro: Dict[str, Any] = Field(default_factory=dict)
    memo: Dict[str, Any] = Field(default_factory=dict)


class InvestigationDetail(InvestigationSummary):
    error_message: str = ""
    verification_notes: str = ""
    da_outcome: Optional[DevilsAdvocateOutcome] = None
    roster: Optional[InvestigationRosterContext] = None
    claims: List[ClaimOut] = Field(default_factory=list)
    evidence_items: List[EvidenceItemOut] = Field(default_factory=list)


class InvestigationCreateRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    trigger_reason: Literal[
        "on_demand",
        "price_move",
        "alert",
        "manual",
        "scheduled",
    ] = "on_demand"
    move_pct: Optional[float] = None
    window_label: str = Field(default="", max_length=64)
    summary: str = Field(default="", max_length=2000)


class EvidenceCreateRequest(BaseModel):
    source_type: str = Field(default="other", max_length=32)
    retrieval_method: str = Field(default="user", max_length=32)
    title: str = Field(default="", max_length=280)
    excerpt: str = Field(default="", max_length=8000)
    source_url: str = Field(default="", max_length=1024)


class ClaimCreateRequest(BaseModel):
    statement: str = Field(..., min_length=1, max_length=4000)
    stance: Literal["supports_move", "contradicts", "market_noise", "unknown"] = "unknown"
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rank: int = Field(default=0, ge=0, le=20)
    devil_advocate_notes: str = Field(default="", max_length=4000)
    evidence_ids: List[int] = Field(default_factory=list)


class ClaimEvidenceLinkRequest(BaseModel):
    claim_id: int
    evidence_id: int
    relation: Literal["supports", "contradicts", "context"] = "supports"
    note: str = Field(default="", max_length=280)


class InvestigationRunRequest(BaseModel):
    window_label: str = Field(default="1d", max_length=64)
    skip_if_noise: bool = False


class InvestigateTickerRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    trigger_reason: Literal[
        "on_demand",
        "price_move",
        "alert",
        "manual",
        "scheduled",
    ] = "on_demand"
    window_label: str = Field(default="1d", max_length=64)
    skip_if_noise: bool = False
    use_trigger_gate: bool = False


class InvestigationSweepRequest(BaseModel):
    dry_run: bool = False
    max_launches: Optional[int] = Field(default=None, ge=1, le=50)


class InvestigationSweepResponse(BaseModel):
    evaluated: int = 0
    launched: int = 0
    skipped_cooldown: int = 0
    skipped_trigger: int = 0
    errors: int = 0
    dry_run: bool = False
    details: List[Dict[str, Any]] = Field(default_factory=list)


class TriggerPreviewResponse(BaseModel):
    ticker: str
    should_investigate: bool
    reason: str
    depth: str
    asset_class: str
    move_pct: Optional[float] = None
    realized_vol_pct: Optional[float] = None
    move_zscore: Optional[float] = None
    benchmark_move_pct: Optional[float] = None
    residual_pct: Optional[float] = None
    window_label: str = "1d"


class InvestigationSearchHit(BaseModel):
    investigation_id: int
    ticker: str
    status: str
    trigger_reason: str = ""
    summary: str = ""
    snippet: str = ""
    score: float = 0.0
    match_sources: List[str] = Field(default_factory=list)
    move_pct: Optional[float] = None
    created_at: str = ""
    claims_count: int = 0
    evidence_count: int = 0


class InvestigationSearchResponse(BaseModel):
    query: str
    mode: str = "keyword"
    results: List[InvestigationSearchHit] = Field(default_factory=list)


class SmartSummaryResponse(BaseModel):
    headline: str = ""
    bullets: List[str] = Field(default_factory=list)
    takeaway: str = ""
    source: str = "heuristic"


class SmartSummarizeTextRequest(BaseModel):
    title: str = Field(default="", max_length=280)
    body: str = Field(..., min_length=1, max_length=20000)


class InvestigationChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)

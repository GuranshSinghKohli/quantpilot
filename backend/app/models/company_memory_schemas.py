"""Phase 8 — company memory types. Additive; does not replace the ledger."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

MemoryType = Literal[
    "COMPANY_FACT",
    "MANAGEMENT_STATEMENT",
    "RISK",
    "EVENT",
    "INVESTIGATION_CONCLUSION",
    "THEME",
]
MemoryStatus = Literal[
    "ACTIVE",
    "CONFIRMED",
    "WEAKENED",
    "CONTRADICTED",
    "OUTDATED",
    "ARCHIVED",
]


class CompanyMemoryItemOut(BaseModel):
    id: int
    ticker: str
    memory_type: str
    statement: str
    theme_key: str = ""
    status: str = "ACTIVE"
    polarity: str = "NEUTRAL"
    observed_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    observation_count: int = 1
    evidence_id: Optional[int] = None
    investigation_id: Optional[int] = None
    source_url: str = ""
    source_type: str = ""
    supersedes_id: Optional[int] = None
    historical: bool = True


class SimilarEventOut(BaseModel):
    investigation_id: int
    ticker: str
    move_pct: Optional[float] = None
    summary: str = ""
    completed_at: Optional[str] = None
    similarity: str = "similar"
    note: str = ""


class CompanyMemorySummary(BaseModel):
    ticker: str
    company_name: str = ""
    last_updated: Optional[str] = None
    recent_themes: List[str] = Field(default_factory=list)
    recurring_risks: List[str] = Field(default_factory=list)
    recent_investigations: List[str] = Field(default_factory=list)
    management_trend: str = ""
    key_facts: List[str] = Field(default_factory=list)
    active_risks: List[CompanyMemoryItemOut] = Field(default_factory=list)
    management_themes: List[CompanyMemoryItemOut] = Field(default_factory=list)
    important_events: List[CompanyMemoryItemOut] = Field(default_factory=list)
    key_claims: List[CompanyMemoryItemOut] = Field(default_factory=list)
    similar_events: List[SimilarEventOut] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CompanyMemoryContext(BaseModel):
    """Compact payload injected into planner / hypotheses. Never includes portfolio."""

    ticker: str = ""
    summary: str = ""
    items: List[CompanyMemoryItemOut] = Field(default_factory=list)
    similar_events: List[SimilarEventOut] = Field(default_factory=list)
    related_investigation_ids: List[int] = Field(default_factory=list)
    management_trend: str = ""
    conflicts: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

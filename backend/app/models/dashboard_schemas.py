"""Phase 10 — additive dashboard / company intelligence payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.alert_schemas import AlertEventOut
from app.models.company_memory_schemas import CompanyMemoryItemOut, CompanyMemorySummary
from app.models.investigation_schemas import InvestigationSummary


class ActivityItem(BaseModel):
    at: str
    ticker: str = ""
    kind: str
    title: str
    detail: str = ""
    investigation_id: Optional[int] = None
    source: str = "ledger"


class DashboardCounts(BaseModel):
    investigations_today: int = 0
    active: int = 0
    completed_today: int = 0
    skipped: int = 0
    alerts_unread: int = 0
    alerts_today: int = 0


class PortfolioSnapshot(BaseModel):
    holdings_count: int = 0
    tickers: List[str] = Field(default_factory=list)
    total_market_value: Optional[float] = None
    weighted_by_real_positions: bool = False
    largest_ticker: Optional[str] = None
    largest_weight_pct: Optional[float] = None
    note: str = ""


class DashboardResponse(BaseModel):
    generated_at: str
    counts: DashboardCounts = Field(default_factory=DashboardCounts)
    portfolio: PortfolioSnapshot = Field(default_factory=PortfolioSnapshot)
    active: List[InvestigationSummary] = Field(default_factory=list)
    recent: List[InvestigationSummary] = Field(default_factory=list)
    alerts: List[AlertEventOut] = Field(default_factory=list)
    activity: List[ActivityItem] = Field(default_factory=list)
    company_updates: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CompanyIntelligenceResponse(BaseModel):
    ticker: str
    memory: CompanyMemorySummary
    investigations: List[InvestigationSummary] = Field(default_factory=list)
    timeline: List[CompanyMemoryItemOut] = Field(default_factory=list)
    related_investigation_ids: List[int] = Field(default_factory=list)
    management_trend: str = ""
    notes: List[str] = Field(default_factory=list)

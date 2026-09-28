"""Structured research I/O. Findings are not evidence; status is not confidence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import ClaimKind, VerificationStatus

ALLOWED_SPECIALISTS = ("news", "company", "technology", "market")
PLAN_VERSION = "plan-v1"


class AgentFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1, max_length=64)
    agent: str = Field(min_length=1, max_length=32)
    statement: str = Field(min_length=1, max_length=2000)
    finding_type: ClaimKind
    evidence_refs: List[str] = Field(default_factory=list, max_length=20)
    citations: List[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    reasoning_summary: str = Field(default="", max_length=500)
    verification_status: Optional[VerificationStatus] = None

    @field_validator("statement", "reasoning_summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class AgentFindingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: List[AgentFinding] = Field(default_factory=list, max_length=20)


class VerifiedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    finding_id: str
    statement: str
    claim_type: ClaimKind
    verification_status: VerificationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    citation_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    agent: str
    issues: List[str] = Field(default_factory=list)


class AgentRunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    model: str
    model_version: str
    prompt_version: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Optional[float] = None
    retry_count: int = 0
    status: str
    error_type: Optional[str] = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any = None
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class MasterPlanPayload(BaseModel):
    """Structured planner output. Agent names outside the fixed enum are rejected."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=500)
    selected_agents: List[str] = Field(default_factory=list, max_length=8)
    evidence_requirements: List[str] = Field(default_factory=list, max_length=8)
    requested_sections: List[str] = Field(default_factory=list, max_length=12)
    reasoning_notes: str = Field(default="", max_length=500)


class SummaryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bullets: List[str] = Field(default_factory=list, max_length=8)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    normalized_request: Dict[str, Any]
    selected_agents: List[str]
    evidence_requirements: List[str] = Field(default_factory=list)
    requested_sections: List[str] = Field(default_factory=list)
    reasoning_notes: str = ""
    plan_version: str = PLAN_VERSION
    source: str = "fallback"


class ReportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    finding_type: ClaimKind
    claim_id: str
    citation_ids: List[str] = Field(default_factory=list)
    agent: str


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    executive_summary: List[ReportItem] = Field(default_factory=list)
    key_facts: Optional[List[ReportItem]] = None
    market_observations: Optional[List[ReportItem]] = None
    company_relationships: Optional[List[ReportItem]] = None
    technology_findings: Optional[List[ReportItem]] = None
    implications: Optional[List[ReportItem]] = None
    scenarios: Optional[List[ReportItem]] = None
    conflicts: Optional[List[ReportItem]] = None
    limitations: List[str] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: Dict[str, Any]
    findings: List[AgentFinding] = Field(default_factory=list)
    verified_claims: List[VerifiedClaim] = Field(default_factory=list)
    unsupported_claims: List[VerifiedClaim] = Field(default_factory=list)
    conflicts: List[VerifiedClaim] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    agent_metadata: List[AgentRunMeta] = Field(default_factory=list)
    verification_summary: Dict[str, Any] = Field(default_factory=dict)
    errors: List[Dict[str, str]] = Field(default_factory=list)
    unavailable_components: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    research_job_id: Optional[uuid.UUID] = None
    research_id: Optional[uuid.UUID] = None
    status: str = "succeeded"
    plan: Optional[ResearchPlan] = None
    report: Optional[ResearchReport] = None


def empty_package(query: str = "") -> Dict[str, Any]:
    return {
        "request": {
            "query": query,
            "entity_ids": [],
            "event_ids": [],
            "date_from": None,
            "date_to": None,
            "document_types": [],
        },
        "entities": [],
        "events": [],
        "market_facts": [],
        "graph_facts": [],
        "documents": [],
        "citations": [],
        "retrieval_metadata": {
            "semantic_available": False,
            "semantic_reason": None,
            "citation_coverage": 1.0,
            "limits": {},
        },
    }


def initial_state(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request": request,
        "evidence_package": empty_package(str(request.get("query") or "")),
        "agent_findings": {},
        "claims": [],
        "verification_results": [],
        "errors": [],
        "metadata": {},
        "plan": {},
        "report": {},
        "result": {},
    }

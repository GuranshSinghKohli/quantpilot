"""Phase 3 — structured investigation plan, tasks, and evidence assessment."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal["pending", "running", "completed", "failed"]
TaskCategory = Literal[
    "news",
    "filings",
    "price",
    "fundamentals",
    "earnings",
    "primary_source",
    "macro",
    "other",
]


class ResearchTask(BaseModel):
    task_id: str
    category: TaskCategory = "other"
    objective: str = ""
    hypothesis_ids: List[str] = Field(default_factory=list)
    required_tool: str
    priority: int = 1
    status: TaskStatus = "pending"
    error: str = ""
    retry_count: int = 0


class ResearchPlan(BaseModel):
    investigation_question: str = ""
    hypotheses: List[str] = Field(default_factory=list)
    tasks: List[ResearchTask] = Field(default_factory=list)
    required_evidence: List[str] = Field(default_factory=list)
    priority: str = "standard"
    expected_sources: List[str] = Field(default_factory=list)
    stopping_conditions: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    need_browser_ir: bool = False
    focus: str = ""


class EvidenceAssessment(BaseModel):
    sufficient_evidence: bool = False
    supported_hypotheses: List[str] = Field(default_factory=list)
    weakened_hypotheses: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    recommended_next_tools: List[str] = Field(default_factory=list)
    failed_tools: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    evidence_counts: Dict[str, int] = Field(default_factory=dict)


class InvestigationGraphResult(BaseModel):
    """In-memory graph output; ledger persist stays in the runner."""

    plan: Dict[str, Any] = Field(default_factory=dict)
    research_plan: Dict[str, Any] = Field(default_factory=dict)
    evidence_rows: List[Dict[str, Any]] = Field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = Field(default_factory=list)
    ranked: Dict[str, Any] = Field(default_factory=dict)
    verified: Dict[str, Any] = Field(default_factory=dict)
    da: Dict[str, Any] = Field(default_factory=dict)
    assessment: Dict[str, Any] = Field(default_factory=dict)
    iteration_count: int = 0
    challenge_loops: int = 0
    status: str = "complete"
    errors: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    incomplete_evidence: bool = False
    engine: Dict[str, Any] = Field(default_factory=dict)
    verification_report: Dict[str, Any] = Field(default_factory=dict)
    final_report: Dict[str, Any] = Field(default_factory=dict)
    conclusion_strength: str = ""
    evidence_gaps: List[str] = Field(default_factory=list)
    company_memory: Dict[str, Any] = Field(default_factory=dict)

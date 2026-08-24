"""Evaluation contracts. Ground truth must never be passed to production agents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Difficulty = Literal["easy", "medium", "hard", "ambiguous"]
MaterialityLabel = Literal["HIGH_MATERIALITY", "MEDIUM_MATERIALITY", "LOW_MATERIALITY"]
Track = Literal[
    "event",
    "investigation",
    "evidence",
    "company",
    "alert",
    "browser",
    "mcp",
    "hallucination",
    "verification",
]


class EvidenceFixture(BaseModel):
    title: str
    excerpt: str = ""
    source_url: str = ""
    source_type: str = "news"
    source_name: str = ""
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    official: bool = False


class HypothesisFixture(BaseModel):
    statement: str
    rank: int = 1
    claim_type: str = "CAUSAL_EXPLANATION"
    evidence_indices: List[int] = Field(default_factory=list)
    contradicting_indices: List[int] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    confidence: str = "MEDIUM"


class ToolCallFixture(BaseModel):
    tool: str
    ok: bool = True
    latency_ms: float = 0.0
    duplicate: bool = False


class MoveFixture(BaseModel):
    ticker: str
    move_pct: float
    window_label: str = "1d"
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    realized_vol_pct: Optional[float] = 2.0
    move_zscore: Optional[float] = None
    benchmark_move_pct: Optional[float] = None
    residual_pct: Optional[float] = None
    source: str = "eval_replay"


class FilingFixture(BaseModel):
    form_type: str
    filing_date: str
    document_url: str = ""
    accession_number: str = ""


class MemoryFixture(BaseModel):
    memory_type: str
    statement: str
    theme_key: str = ""
    polarity: str = "NEUTRAL"
    observed_at: str
    source_url: str = ""
    source_type: str = "ir_page"


class EvaluationCase(BaseModel):
    """Public case card. Does not contain acceptable answers."""

    case_id: str
    ticker: str
    event_timestamp: str
    cutoff_time: str
    event_type: str
    event_description: str
    track: Track = "investigation"
    difficulty: Difficulty = "medium"
    tags: List[str] = Field(default_factory=list)
    source_notes: str = ""
    allow_retrospective: bool = False


class GroundTruth(BaseModel):
    """Evaluator-only. Never supplied to production investigation code."""

    expected_event_types: List[str] = Field(default_factory=list)
    should_detect: bool = True
    expected_materiality: MaterialityLabel = "MEDIUM_MATERIALITY"
    should_investigate: bool = True
    should_notify: bool = False
    acceptable_hypothesis_phrases: List[str] = Field(default_factory=list)
    unacceptable_hypothesis_phrases: List[str] = Field(default_factory=list)
    required_source_domains: List[str] = Field(default_factory=list)
    known_contradictions: List[str] = Field(default_factory=list)
    expected_conclusion_strengths: List[str] = Field(default_factory=list)
    expected_memory_change: bool = False
    primary_source_required: bool = False
    planted_flaws: List[str] = Field(default_factory=list)
    expected_browser_domain: str = ""
    should_use_browser: Optional[bool] = None
    notes: str = ""


class ReplayFixture(BaseModel):
    move: Optional[MoveFixture] = None
    filings: List[FilingFixture] = Field(default_factory=list)
    fundamentals: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[EvidenceFixture] = Field(default_factory=list)
    hypotheses: List[HypothesisFixture] = Field(default_factory=list)
    memory: List[MemoryFixture] = Field(default_factory=list)
    tool_trace: List[ToolCallFixture] = Field(default_factory=list)
    browser_found_url: str = ""
    latency_ms: Dict[str, float] = Field(default_factory=dict)
    llm_calls: int = 0
    llm_tokens: int = 0
    exposure: float = 0.0
    trigger_reason: str = "scheduled"
    investigation_status: str = "complete"
    claims_grounded: bool = True


class PackedCase(BaseModel):
    case: EvaluationCase
    truth: GroundTruth
    fixture: ReplayFixture


class CaseScore(BaseModel):
    case_id: str
    track: str
    metrics: Dict[str, float] = Field(default_factory=dict)
    flags: List[str] = Field(default_factory=list)
    failures: List[Dict[str, Any]] = Field(default_factory=list)
    actual: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: Dict[str, float] = Field(default_factory=dict)
    tool_calls: int = 0
    sources_used: List[str] = Field(default_factory=list)
    hypotheses: List[str] = Field(default_factory=list)
    conclusion: str = ""


class EvaluationRun(BaseModel):
    run_id: str
    git_commit: str
    dataset_version: str
    benchmark_version: str
    model: str
    timestamp: datetime
    configuration: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, float] = Field(default_factory=dict)
    cases: List[CaseScore] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    failures: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)

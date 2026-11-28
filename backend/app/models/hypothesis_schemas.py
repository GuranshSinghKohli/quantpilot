"""Phase 4 — in-memory hypothesis engine models.

Claims in the Evidence Ledger remain the persisted representation.
These types are not a second database.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

HypothesisCategory = Literal[
    "COMPANY_SPECIFIC",
    "EARNINGS",
    "GUIDANCE",
    "MACRO",
    "REGULATORY",
    "COMPETITOR",
    "INDUSTRY",
    "MARKET_WIDE",
    "TECHNICAL",
    "SENTIMENT",
    "OTHER",
]

HypothesisStatus = Literal[
    "OPEN",
    "INVESTIGATING",
    "SUPPORTED",
    "WEAKENED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
    "DISCARDED",
]

EvidenceRelationKind = Literal["supports", "contradicts", "neutral", "insufficient"]
SourceQuality = Literal["PRIMARY", "HIGH", "MEDIUM", "LOW"]
TemporalLabel = Literal[
    "pre_event",
    "same_day",
    "post_event",
    "retrospective",
    "unknown",
]
ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "VERY_LOW"]


class EvidenceRequirement(BaseModel):
    description: str
    tool_categories: List[str] = Field(default_factory=list)
    polarity: Literal["support", "contradict"] = "support"


class HypothesisRecord(BaseModel):
    hypothesis_id: str
    title: str
    description: str = ""
    category: HypothesisCategory = "OTHER"
    status: HypothesisStatus = "OPEN"
    prior_support: float = 0.25
    current_support: float = 0.25
    support_score: float = 0.25
    confidence_level: ConfidenceLevel = "LOW"
    rank: int = 0
    why_plausible: str = ""
    required_evidence: List[str] = Field(default_factory=list)
    contradicting_requirements: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    supporting_evidence: List[int] = Field(default_factory=list)
    contradicting_evidence: List[int] = Field(default_factory=list)
    neutral_evidence: List[int] = Field(default_factory=list)
    tool_categories: List[str] = Field(default_factory=list)
    discard_reason: str = ""
    stance: str = "unknown"


class EvidenceClassification(BaseModel):
    evidence_index: int
    hypothesis_id: str
    relation: EvidenceRelationKind
    quality: SourceQuality = "MEDIUM"
    independence_group: str = ""
    temporal: TemporalLabel = "unknown"
    weight: float = 0.0
    note: str = ""


class HypothesisEngineResult(BaseModel):
    hypotheses: List[HypothesisRecord] = Field(default_factory=list)
    classifications: List[EvidenceClassification] = Field(default_factory=list)
    leading_hypothesis_id: str = ""
    strongest_alternative_id: str = ""
    weakest_hypothesis_id: str = ""
    summary: str = ""
    created_hypothesis_ids: List[str] = Field(default_factory=list)
    discarded_hypothesis_ids: List[str] = Field(default_factory=list)


class HypothesisContext(BaseModel):
    """Additive payload for verification / Devil's Advocate."""

    leading_hypothesis_id: str = ""
    strongest_alternative_id: str = ""
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    contradicting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    source_quality: List[str] = Field(default_factory=list)
    confidence_level: str = "LOW"
    support_score: Optional[float] = None

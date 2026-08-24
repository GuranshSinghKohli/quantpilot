"""Phase 5 — in-memory evidence intelligence types.

Persisted rows remain investigations / claims / evidence_items / claim_evidence_links.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SourceClass = Literal["PRIMARY", "SECONDARY", "DERIVED", "MARKET", "OTHER"]
SourceTaxonomy = Literal[
    "PRIMARY_COMPANY",
    "SEC_FILING",
    "GOVERNMENT",
    "MARKET_DATA",
    "ECONOMIC_DATA",
    "EARNINGS_TRANSCRIPT",
    "FINANCIAL_NEWS",
    "SECONDARY_RESEARCH",
    "OTHER",
]
SourceTier = Literal["TIER_1", "TIER_2", "TIER_3", "TIER_4"]
RelevanceLevel = Literal["HIGH", "MEDIUM", "LOW"]
GraphTemporal = Literal["PRE_EVENT", "EVENT_TIME", "POST_EVENT", "UNKNOWN"]
ClaimType = Literal[
    "FACT",
    "INTERPRETATION",
    "CAUSAL_EXPLANATION",
    "RISK",
    "CONCLUSION",
]
ClaimSupportStatus = Literal[
    "UNSUPPORTED",
    "PARTIALLY_SUPPORTED",
    "SUPPORTED",
    "STRONGLY_SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
]
ConflictKind = Literal[
    "DIRECT_CONTRADICTION",
    "CONTEXTUAL_DIFFERENCE",
    "TEMPORAL_DIFFERENCE",
    "NO_CONFLICT",
]


class EvidenceProvenance(BaseModel):
    source_name: str = ""
    source_url: str = ""
    source_type: str = "other"
    source_taxonomy: SourceTaxonomy = "OTHER"
    source_class: SourceClass = "OTHER"
    source_tier: SourceTier = "TIER_3"
    source_quality: str = "LOW"
    retrieval_method: str = "system"
    published_at: Optional[str] = None
    retrieved_at: Optional[str] = None
    content_hash: str = ""
    source_group: str = ""
    temporal_relevance: GraphTemporal = "UNKNOWN"


class ClaimSupportAssessment(BaseModel):
    status: ClaimSupportStatus = "UNSUPPORTED"
    claim_type: ClaimType = "CAUSAL_EXPLANATION"
    supporting_count: int = 0
    contradicting_count: int = 0
    context_count: int = 0
    independent_support: int = 0
    primary_support: int = 0
    relevance: RelevanceLevel = "LOW"
    temporal_relevance: GraphTemporal = "UNKNOWN"
    conflict: ConflictKind = "NO_CONFLICT"
    conflict_note: str = ""
    gaps: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    total_evidence: int = 0
    primary: int = 0
    secondary: int = 0
    derived: int = 0
    contextual: int = 0
    claims: int = 0
    strongly_supported: int = 0
    supported: int = 0
    partially_supported: int = 0
    insufficient: int = 0
    contradicted: int = 0
    unsupported: int = 0
    primary_source_coverage: float = 0.0
    evidence_gaps: int = 0
    conflicts: int = 0


class EvidenceGraphSnapshot(BaseModel):
    summary: EvidenceSummary = Field(default_factory=EvidenceSummary)
    assessments: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)

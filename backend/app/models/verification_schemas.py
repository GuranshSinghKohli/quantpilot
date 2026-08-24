"""Phase 7 — structured verification and Devil's Advocate results.

Persisted only as additive JSON on existing investigation rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ChallengeSeverity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ChallengeRecommendation = Literal["ACCEPT", "REVIEW", "RESEARCH_MORE", "REJECT"]
ConclusionStrength = Literal[
    "ROBUST",
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "UNCERTAIN",
    "INSUFFICIENT_EVIDENCE",
    "CONTRADICTED",
]
ConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW"]


class SourceCheck(BaseModel):
    evidence_index: int
    ok: bool = True
    issues: List[str] = Field(default_factory=list)
    source_type: str = ""
    source_url: str = ""
    source_group: str = ""
    temporal_relevance: str = ""


class ClaimEvidenceCheck(BaseModel):
    statement: str = ""
    claim_type: str = "CAUSAL_EXPLANATION"
    ok: bool = True
    issues: List[str] = Field(default_factory=list)
    support_status: str = ""
    contradictions: List[str] = Field(default_factory=list)
    retrospective: bool = False
    independent_support: int = 0


class HypothesisCheck(BaseModel):
    leading_supported: bool = False
    alternative_considered: bool = False
    contradictions_considered: bool = False
    gaps: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class ConclusionCheck(BaseModel):
    causal_justified: bool = False
    uncertainty_represented: bool = False
    hedged_statements: List[str] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)


class VerificationSummary(BaseModel):
    claims_reviewed: int = 0
    supported: int = 0
    partially_supported: int = 0
    unsupported: int = 0
    contradicted: int = 0
    primary_source_coverage: ConfidenceLabel = "LOW"
    major_contradiction: str = ""
    devil_advocate_severity: str = ""
    final_conclusion: str = ""
    conclusion_strength: ConclusionStrength = "UNCERTAIN"
    confidence: ConfidenceLabel = "LOW"


class EvidenceGapReport(BaseModel):
    gaps: List[str] = Field(default_factory=list)
    only_secondary_news: bool = False
    missing_primary: bool = False
    missing_management_commentary: bool = False
    missing_regulatory: bool = False


class FinalInvestigationReport(BaseModel):
    """Backend data contract for the final investigation. Not a frontend redesign."""

    what_happened: str = ""
    why_it_likely_happened: str = ""
    strongest_alternative: str = ""
    supporting_evidence: List[str] = Field(default_factory=list)
    contradicting_evidence: List[str] = Field(default_factory=list)
    verification: VerificationSummary = Field(default_factory=VerificationSummary)
    devils_advocate: Dict[str, Any] = Field(default_factory=dict)
    evidence_gaps: List[str] = Field(default_factory=list)
    portfolio_impact: str = ""
    final_assessment: str = ""
    conclusion_strength: ConclusionStrength = "UNCERTAIN"
    confidence: ConfidenceLabel = "LOW"
    sources: List[str] = Field(default_factory=list)
    trading_advice: str = ""


class VerificationReport(BaseModel):
    source_checks: List[SourceCheck] = Field(default_factory=list)
    claim_checks: List[ClaimEvidenceCheck] = Field(default_factory=list)
    hypothesis_check: HypothesisCheck = Field(default_factory=HypothesisCheck)
    conclusion_check: ConclusionCheck = Field(default_factory=ConclusionCheck)
    notes: List[str] = Field(default_factory=list)
    passed: bool = True
    summary: VerificationSummary = Field(default_factory=VerificationSummary)
    evidence_gaps: EvidenceGapReport = Field(default_factory=EvidenceGapReport)
    conclusion_strength: ConclusionStrength = "UNCERTAIN"
    research_more_reasons: List[str] = Field(default_factory=list)
    rejected_claims: List[str] = Field(default_factory=list)


class DevilsAdvocateResult(BaseModel):
    target_hypothesis_id: str = ""
    strongest_counterargument: str = ""
    contradictory_evidence: List[str] = Field(default_factory=list)
    ignored_evidence: List[str] = Field(default_factory=list)
    questionable_assumptions: List[str] = Field(default_factory=list)
    alternative_hypothesis_id: str = ""
    alternative_strength: str = ""
    additional_evidence_needed: List[str] = Field(default_factory=list)
    challenge_severity: ChallengeSeverity = "LOW"
    recommendation: ChallengeRecommendation = "ACCEPT"
    overweighted_evidence: str = ""
    notes: List[str] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

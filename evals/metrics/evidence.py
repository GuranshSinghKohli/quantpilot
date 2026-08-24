"""Evidence quality rates. Deterministic."""

from __future__ import annotations

from typing import Sequence
from urllib.parse import urlparse

from evals.metrics.hypotheses import phrase_match, tokens
from evals.schemas import EvidenceFixture


_PRIMARY = {"filing", "ir_page", "transcript"}
_OFFICIAL_HINTS = (
    "sec.gov",
    "investor.",
    "ir.",
    "nvidia.com",
    "apple.com",
    "microsoft.com",
    "meta.com",
    "amazon.com",
    "tesla.com",
    "federalreserve.gov",
    "bls.gov",
)


def _primary(item: EvidenceFixture) -> bool:
    if item.official or item.source_type in _PRIMARY:
        return True
    host = (urlparse(item.source_url).hostname or "").lower()
    return any(h in host for h in _OFFICIAL_HINTS)


def evidence_metrics(
    evidence: Sequence[EvidenceFixture],
    statements: Sequence[str],
    contradictions: Sequence[str],
) -> dict:
    if not evidence:
        return {
            "primary_source_rate": 0.0,
            "evidence_relevance": 0.0,
            "claim_support_rate": 0.0,
            "contradiction_coverage": 0.0,
            "evidence_duplication": 0.0,
        }
    primary = sum(1 for e in evidence if _primary(e))
    corpus = " ".join(e.title + " " + e.excerpt for e in evidence)
    relevant = 0
    for stmt in statements:
        if any(phrase_match(corpus, stmt) or phrase_match(stmt, e.title) for e in evidence):
            relevant += 1
    n_s = max(1, len(statements))
    contra_hit = 0
    for phrase in contradictions:
        if any(phrase_match(e.title + " " + e.excerpt, phrase) for e in evidence):
            contra_hit += 1
    urls = [e.source_url for e in evidence if e.source_url]
    dup = 1.0 - (len(set(urls)) / len(urls)) if urls else 0.0
    return {
        "primary_source_rate": primary / len(evidence),
        "evidence_relevance": relevant / n_s,
        "claim_support_rate": relevant / n_s,
        "contradiction_coverage": (
            contra_hit / len(contradictions) if contradictions else 1.0
        ),
        "evidence_duplication": dup,
    }

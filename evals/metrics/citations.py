"""Citation existence, URL shape, and temporal validity. Deterministic."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence
from urllib.parse import urlparse

from evals.schemas import EvidenceFixture, HypothesisFixture
from evals.temporal import is_future


_PLACEHOLDER = ("example.com", "localhost", "invalid", "about:blank")


def url_ok(url: str) -> bool:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return not any(p in host for p in _PLACEHOLDER)


def citation_metrics(
    hypotheses: Sequence[HypothesisFixture],
    evidence: Sequence[EvidenceFixture],
    cutoff: datetime,
    *,
    allow_retrospective: bool = False,
) -> dict:
    cited: list = []
    for hyp in hypotheses:
        cited.extend(hyp.citations)
        for idx in hyp.evidence_indices:
            if 0 <= idx < len(evidence):
                cited.append(evidence[idx].source_url)
    if not cited:
        return {
            "citation_validity": 1.0 if not hypotheses else 0.0,
            "citation_support_accuracy": 0.0,
            "citation_completeness": 0.0,
            "fabricated_citation_rate": 0.0,
        }
    pool = {e.source_url for e in evidence if e.source_url}
    valid = 0
    fabricated = 0
    dated_ok = 0
    dated = 0
    for url in cited:
        if url_ok(url) and url in pool:
            valid += 1
        if url and url not in pool:
            fabricated += 1
        match = next((e for e in evidence if e.source_url == url), None)
        if match and match.published_at:
            dated += 1
            if allow_retrospective or not is_future(match.published_at, cutoff):
                dated_ok += 1
    supported = 0
    for hyp in hypotheses:
        if hyp.evidence_indices or any(c in pool for c in hyp.citations):
            supported += 1
    n_h = max(1, len(hypotheses))
    return {
        "citation_validity": valid / len(cited),
        "citation_support_accuracy": dated_ok / dated if dated else 1.0,
        "citation_completeness": supported / n_h,
        "fabricated_citation_rate": fabricated / len(cited),
    }

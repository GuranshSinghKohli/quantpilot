"""Minimal single-agent baseline. Not a second product.

Event + first available excerpts → one unhedged conclusion.
No verification, Devil's Advocate, memory, or temporal filter.
"""

from __future__ import annotations

from typing import List

from evals.schemas import EvidenceFixture, HypothesisFixture, PackedCase


def run_baseline(packed: PackedCase) -> List[HypothesisFixture]:
    evidence = list(packed.fixture.evidence)
    snippet = evidence[0].title if evidence else packed.case.event_description
    urls = [e.source_url for e in evidence if e.source_url]
    statement = (
        f"{packed.case.ticker} moved because {snippet}. "
        "This fully explains the price action."
    )
    return [
        HypothesisFixture(
            statement=statement,
            rank=1,
            claim_type="CAUSAL_EXPLANATION",
            evidence_indices=list(range(min(3, len(evidence)))),
            citations=urls[:5],
            confidence="HIGH",
        )
    ]

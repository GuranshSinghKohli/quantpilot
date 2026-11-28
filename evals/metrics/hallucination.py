"""Hallucination / uncertainty. Uses verifier statuses, not prose quality."""

from __future__ import annotations

from typing import Sequence


def hallucination_metrics(
    *,
    claim_statuses: Sequence[str],
    fabricated_citation_rate: float,
    expected_uncertain: bool,
    conclusion_strength: str,
) -> dict:
    n = len(claim_statuses) or 1
    unsupported = sum(
        1
        for s in claim_statuses
        if str(s).upper() in {"UNSUPPORTED", "CONTRADICTED"}
    )
    uncertain = str(conclusion_strength).upper() in {
        "UNCERTAIN",
        "INSUFFICIENT_EVIDENCE",
    }
    appropriate = 1.0 if (expected_uncertain == uncertain) or not expected_uncertain else (
        1.0 if uncertain else 0.0
    )
    if expected_uncertain:
        appropriate = 1.0 if uncertain else 0.0
    return {
        "unsupported_claim_rate": unsupported / n,
        "hallucination_rate": max(fabricated_citation_rate, unsupported / n),
        "appropriate_uncertainty_rate": appropriate,
    }

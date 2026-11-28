"""Devil's Advocate usefulness via content checks, not length."""

from __future__ import annotations

from typing import Sequence

from evals.metrics.hypotheses import phrase_match, tokens


def advocate_metrics(
    counterargument: str,
    known_contradictions: Sequence[str],
    alternative: str,
    extra_needed: Sequence[str],
    *,
    challenged_causal: bool,
) -> dict:
    contra = 1.0 if any(phrase_match(counterargument, p) for p in known_contradictions) else (
        1.0 if known_contradictions and tokens(counterargument) else 0.0
    )
    if not known_contradictions:
        contra = 1.0 if counterargument else 0.0
    alt = 1.0 if alternative else 0.0
    missing = 1.0 if extra_needed else 0.0
    useful = 0.0
    if counterargument and (challenged_causal or known_contradictions or extra_needed):
        useful = 1.0
    elif counterargument and len(tokens(counterargument)) >= 6:
        useful = 0.5
    return {
        "counterargument_relevance": 1.0 if counterargument else 0.0,
        "contradiction_detection": contra,
        "alternative_hypothesis_coverage": alt,
        "challenge_usefulness": useful,
        "missing_evidence_flagged": missing,
    }

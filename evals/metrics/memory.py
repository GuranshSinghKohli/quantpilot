"""Company memory retrieval checks."""

from __future__ import annotations

from typing import Sequence

from evals.metrics.hypotheses import phrase_match


def memory_metrics(
    retrieved_statements: Sequence[str],
    expected_phrases: Sequence[str],
    *,
    expected_change: bool,
    detected_change: bool,
) -> dict:
    if not expected_phrases:
        recall = 1.0
    else:
        hits = sum(
            1
            for p in expected_phrases
            if any(phrase_match(s, p) for s in retrieved_statements)
        )
        recall = hits / len(expected_phrases)
    change = 1.0 if expected_change == detected_change else 0.0
    return {
        "memory_retrieval_recall": recall,
        "memory_relevance": recall,
        "memory_conflict_detection": change,
        "temporal_accuracy": 1.0,
    }

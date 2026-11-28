"""Hypothesis coverage via token overlap. Not an LLM judge."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "for",
    "in",
    "on",
    "at",
    "by",
    "with",
    "from",
    "that",
    "this",
    "was",
    "were",
    "is",
    "are",
    "be",
    "as",
    "it",
}


def tokens(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 2}


def phrase_match(statement: str, phrase: str, *, min_overlap: float = 0.6) -> bool:
    need = tokens(phrase)
    have = tokens(statement)
    if not need:
        return False
    if need <= have:
        return True
    return len(need & have) / len(need) >= min_overlap


def hypothesis_recall(statements: Sequence[str], acceptable: Sequence[str]) -> float:
    if not acceptable:
        return 1.0
    return 1.0 if any(any(phrase_match(s, p) for p in acceptable) for s in statements) else 0.0


def top_k_coverage(statements: Sequence[str], acceptable: Sequence[str], k: int) -> float:
    if not acceptable:
        return 1.0
    window = list(statements)[:k]
    return 1.0 if any(any(phrase_match(s, p) for p in acceptable) for s in window) else 0.0


def unsupported_rate(statements: Sequence[str], unacceptable: Sequence[str]) -> float:
    if not statements:
        return 0.0
    hits = sum(1 for s in statements if any(phrase_match(s, p) for p in unacceptable))
    return hits / len(statements)


def redundancy(statements: Sequence[str]) -> float:
    if len(statements) < 2:
        return 0.0
    dups = 0
    pairs = 0
    for i, a in enumerate(statements):
        for b in statements[i + 1 :]:
            pairs += 1
            ta, tb = tokens(a), tokens(b)
            if ta and tb and len(ta & tb) / len(ta | tb) >= 0.75:
                dups += 1
    return dups / pairs if pairs else 0.0


def score_hypotheses(
    statements: Iterable[str],
    acceptable: Sequence[str],
    unacceptable: Sequence[str],
) -> dict:
    rows = list(statements)
    return {
        "hypothesis_recall": hypothesis_recall(rows, acceptable),
        "top1_coverage": top_k_coverage(rows, acceptable, 1),
        "top3_coverage": top_k_coverage(rows, acceptable, 3),
        "top5_coverage": top_k_coverage(rows, acceptable, 5),
        "unsupported_hypothesis_rate": unsupported_rate(rows, unacceptable),
        "redundancy": redundancy(rows),
    }

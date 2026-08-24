"""Did verification catch planted flaws? Deterministic."""

from __future__ import annotations

from typing import Iterable, Sequence


_FLAW_HINTS = {
    "CAUSAL_OVERREACH": ("causal", "overreach", "caused", "does not establish"),
    "TEMPORAL_LEAK": ("retrospective", "post-event", "temporal"),
    "FABRICATED_CITATION": ("url", "missing", "fabricat", "invalid"),
    "UNSUPPORTED": ("unsupported", "insufficient"),
    "CONTRADICTION": ("contradict",),
}


def verification_metrics(
    planted_flaws: Sequence[str],
    issues: Iterable[str],
    *,
    flagged_unsupported: bool,
    flagged_temporal: bool,
) -> dict:
    blob = " ".join(issues).lower()
    caught = 0
    for flaw in planted_flaws:
        hints = _FLAW_HINTS.get(flaw, (flaw.lower(),))
        hit = any(h in blob for h in hints)
        if flaw == "TEMPORAL_LEAK":
            hit = hit or flagged_temporal
        if flaw == "UNSUPPORTED":
            hit = hit or flagged_unsupported
        if hit:
            caught += 1
    recall = caught / len(planted_flaws) if planted_flaws else 1.0
    precision = 1.0 if caught or not planted_flaws else 0.0
    if planted_flaws and caught == 0:
        precision = 0.0
    return {
        "verification_recall": recall,
        "verification_precision": precision,
        "planted_flaws_caught": float(caught),
    }

"""Final conclusion strength vs allowed labels."""

from __future__ import annotations

from typing import Sequence


def conclusion_metrics(strength: str, allowed: Sequence[str]) -> dict:
    label = (strength or "").upper()
    ok = 1.0 if (not allowed or label in {a.upper() for a in allowed}) else 0.0
    return {
        "conclusion_label_match": ok,
        "definitive_forced": 1.0
        if label in {"ROBUST", "SUPPORTED"} and "INSUFFICIENT_EVIDENCE" in {
            a.upper() for a in allowed
        }
        else 0.0,
    }

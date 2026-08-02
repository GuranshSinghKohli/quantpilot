"""PRD v3 Phase 12 — materiality bar for investigation notifications."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from app.services.move_detector import MoveSnapshot

# Notify bar is stricter than investigate bar (Phase 11).
NOTIFY_MIN_SCORE = float(os.getenv("INVESTIGATION_NOTIFY_MIN_SCORE", "45"))
NOTIFY_MIN_Z = float(os.getenv("INVESTIGATION_NOTIFY_MIN_Z", "2.0"))
NOTIFY_MIN_DEPTH = (
    os.getenv("INVESTIGATION_NOTIFY_MIN_DEPTH", "standard") or "standard"
).strip().lower()

_DEPTH_RANK = {"light": 1, "standard": 2, "deep": 3}


@dataclass
class MaterialityAssessment:
    score: float
    should_notify: bool
    reason: str
    depth: str
    move_zscore: Optional[float]
    residual_pct: Optional[float]
    move_pct: Optional[float]


def assess_materiality(
    move: MoveSnapshot,
    *,
    min_score: Optional[float] = None,
    min_z: Optional[float] = None,
    min_depth: Optional[str] = None,
) -> MaterialityAssessment:
    """Score a completed investigation's move for notification eligibility."""
    depth = (getattr(move, "depth", None) or "standard").lower()
    z = getattr(move, "move_zscore", None)
    residual = getattr(move, "residual_pct", None)
    move_pct = move.move_pct

    abs_z = abs(z) if z is not None else None
    abs_r = abs(residual) if residual is not None else None
    abs_m = abs(move_pct) if move_pct is not None else 0.0

    score = 0.0
    score += min(40.0, (abs_z or 0.0) * 12.0)
    score += min(30.0, (abs_r or 0.0) * 6.0)
    score += min(20.0, abs_m * 2.5)
    score += {"light": 0.0, "standard": 8.0, "deep": 18.0}.get(depth, 8.0)
    score = round(min(100.0, score), 2)

    bar_score = NOTIFY_MIN_SCORE if min_score is None else min_score
    bar_z = NOTIFY_MIN_Z if min_z is None else min_z
    bar_depth = (min_depth or NOTIFY_MIN_DEPTH).lower()

    depth_ok = _DEPTH_RANK.get(depth, 0) >= _DEPTH_RANK.get(bar_depth, 2)
    z_ok = abs_z is not None and abs_z >= bar_z
    score_ok = score >= bar_score

    # Notify if score clears the bar AND (depth or z clears a secondary gate).
    should = score_ok and (depth_ok or z_ok)
    if move_pct is None:
        should = False
        reason = "No resolved move; skip notification."
    elif not score_ok:
        reason = f"Materiality score {score:.1f} below notify bar {bar_score:.1f}."
    elif not (depth_ok or z_ok):
        reason = (
            f"Score {score:.1f} ok but depth={depth} / z={abs_z} "
            f"below notify gates (min_depth={bar_depth}, min_z={bar_z})."
        )
    else:
        bits = [f"score={score:.1f}", f"depth={depth}"]
        if abs_z is not None:
            bits.append(f"z={abs_z:.2f}")
        if abs_r is not None:
            bits.append(f"residual={residual:+.2f}%")
        reason = "Material: " + ", ".join(bits)

    return MaterialityAssessment(
        score=score,
        should_notify=should,
        reason=reason,
        depth=depth,
        move_zscore=z,
        residual_pct=residual,
        move_pct=move_pct,
    )

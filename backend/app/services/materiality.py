"""Materiality: investigation gate (Phase 2) and notification bar (Phase 12)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

from app.models.event_schemas import (
    EventSeverity,
    EventType,
    InvestigationMateriality,
    MarketEvent,
)
from app.services.move_detector import MoveSnapshot
from app.services.portfolio_exposure import event_impact

# Notify bar is stricter than investigate bar (Phase 11 / 12).
NOTIFY_MIN_SCORE = float(os.getenv("INVESTIGATION_NOTIFY_MIN_SCORE", "45"))
NOTIFY_MIN_Z = float(os.getenv("INVESTIGATION_NOTIFY_MIN_Z", "2.0"))
NOTIFY_MIN_DEPTH = (
    os.getenv("INVESTIGATION_NOTIFY_MIN_DEPTH", "standard") or "standard"
).strip().lower()

INVESTIGATION_MATERIALITY_THRESHOLD = float(
    os.getenv("INVESTIGATION_MATERIALITY_THRESHOLD", "40")
)
INVESTIGATION_EXPOSURE_WEIGHT = float(os.getenv("INVESTIGATION_EXPOSURE_WEIGHT", "100"))
INVESTIGATION_IDIO_PCT = float(os.getenv("INVESTIGATION_IDIO_PCT", "1.0"))

_DEPTH_RANK = {"light": 1, "standard": 2, "deep": 3}

_FILING_BASE = {
    "8-K": 42.0,
    "8-K/A": 38.0,
    "6-K": 36.0,
    "10-K": 32.0,
    "10-Q": 30.0,
    "20-F": 32.0,
}


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


def assess_investigation_materiality(
    event: MarketEvent,
    *,
    threshold: Optional[float] = None,
    exposure: Optional[float] = None,
) -> InvestigationMateriality:
    """
    Deterministic pre-investigation score.

    Answers: should we spend tools/LLM on this event?
    Does not decide whether to notify the user.
    """
    bar = (
        INVESTIGATION_MATERIALITY_THRESHOLD if threshold is None else float(threshold)
    )
    weight = event.portfolio_exposure if exposure is None else exposure
    if weight is None:
        weight = 0.0
    weight = max(0.0, min(1.0, float(weight)))

    factors: Dict[str, float] = {}
    score = 0.0

    event_base, type_note = _event_type_base(event)
    factors["event_type"] = event_base
    score += event_base

    abs_z = abs(event.z_score) if event.z_score is not None else 0.0
    abs_r = abs(event.residual_pct) if event.residual_pct is not None else 0.0
    abs_m = abs(event.magnitude) if event.magnitude is not None else 0.0

    z_pts = min(35.0, abs_z * 10.0)
    r_pts = min(30.0, abs_r * 5.0)
    m_pts = min(15.0, abs_m * 1.5)
    factors["z_score"] = round(z_pts, 2)
    factors["residual"] = round(r_pts, 2)
    factors["magnitude"] = round(m_pts, 2)
    score += z_pts + r_pts + m_pts

    exp_pts = min(25.0, weight * INVESTIGATION_EXPOSURE_WEIGHT)
    factors["exposure"] = round(exp_pts, 2)
    score += exp_pts

    impact = event_impact(event.magnitude, weight)
    factors["event_impact"] = round(impact, 3)

    market_wide = _is_market_wide_price(event, abs_r, abs_m)
    if market_wide:
        score *= 0.35
        factors["market_wide_haircut"] = 0.35

    score = round(min(100.0, max(0.0, score)), 2)
    depth = _depth_from_score(score, event)
    should = score >= bar and not market_wide

    if market_wide:
        reason = (
            f"Skipped {event.ticker}: movement tracks the benchmark "
            f"(residual {event.residual_pct:+.2f}% < {INVESTIGATION_IDIO_PCT:.1f}% "
            f"idiosyncratic bar); score {score:.1f} after haircut."
        )
        should = False
    elif not should:
        reason = (
            f"Skipped {event.ticker} {event.event_type.value}: "
            f"investigation materiality {score:.1f} below gate {bar:.1f}. "
            f"{type_note}"
        )
    else:
        bits = [
            f"{event.event_type.value}",
            f"score={score:.1f}",
            f"depth={depth}",
        ]
        if event.magnitude is not None:
            bits.append(f"move={event.magnitude:+.2f}%")
        if event.residual_pct is not None:
            bits.append(f"residual={event.residual_pct:+.2f}%")
        if event.z_score is not None:
            bits.append(f"z={event.z_score:.2f}")
        if weight > 0:
            bits.append(f"exposure={weight:.1%}")
        reason = f"Investigating {event.ticker} because: " + "; ".join(bits)

    return InvestigationMateriality(
        score=score,
        should_investigate=should,
        reason=reason,
        depth=depth,
        exposure=weight,
        event_impact=round(impact, 3),
        primary_event_type=event.event_type.value,
        factors=factors,
    )


def _event_type_base(event: MarketEvent) -> tuple:
    if event.event_type == EventType.FILING:
        form = str((event.metadata or {}).get("filing_type") or "").upper()
        base = _FILING_BASE.get(form, 22.0)
        return base, f"{form or 'filing'} disclosure."
    if event.event_type == EventType.EARNINGS:
        return 38.0, "earnings window."
    if event.event_type == EventType.IDIOSYNCRATIC_MOVE:
        return 8.0, "idiosyncratic price move."
    if event.event_type == EventType.VOLATILITY_SPIKE:
        return 6.0, "volatility spike."
    if event.event_type == EventType.PRICE_MOVE:
        return 4.0, "price move."
    return 5.0, event.event_type.value


def _is_market_wide_price(event: MarketEvent, abs_r: float, abs_m: float) -> bool:
    if event.event_type in {EventType.FILING, EventType.EARNINGS, EventType.NEWS}:
        return False
    if event.residual_pct is None:
        return False
    return abs_r < INVESTIGATION_IDIO_PCT and abs_m < max(3.0, INVESTIGATION_IDIO_PCT * 3)


def _depth_from_score(score: float, event: MarketEvent) -> str:
    if score >= 70 or event.severity == EventSeverity.HIGH:
        return "deep"
    if score >= 45 or event.severity == EventSeverity.MEDIUM:
        return "standard"
    return "light"


def pick_primary_event(events: list) -> Optional[MarketEvent]:
    """Highest investigation-priority event (filings/earnings beat beta noise)."""
    if not events:
        return None

    def _key(ev: MarketEvent) -> tuple:
        rank = {
            EventType.FILING: 4,
            EventType.EARNINGS: 3,
            EventType.IDIOSYNCRATIC_MOVE: 2,
            EventType.VOLATILITY_SPIKE: 2,
            EventType.PRICE_MOVE: 1,
        }.get(ev.event_type, 0)
        mag = abs(ev.magnitude or 0.0)
        return (rank, mag)

    return max(events, key=_key)

"""PRD v3 Phase 11 — idiosyncratic + volatility-adjusted investigation triggers."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.observability.logger import get_logger, log_event
from app.services import asset_class as asset_class_service
from app.services.move_detector import MoveSnapshot, as_float, detect_move, safe_tool

logger = get_logger("trigger_logic")

VOL_Z_THRESHOLD = float(os.getenv("INVESTIGATION_VOL_Z_THRESHOLD", "1.5"))
IDIO_PCT = float(os.getenv("INVESTIGATION_IDIO_PCT", "1.0"))
BENCHMARK = (os.getenv("INVESTIGATION_BENCHMARK", "SPY") or "SPY").upper().strip()
UPSIDE_Z_FOR_DEEP = float(os.getenv("INVESTIGATION_UPSIDE_Z_DEEP", "2.0"))
DOWNSIDE_Z_FOR_DEEP = float(os.getenv("INVESTIGATION_DOWNSIDE_Z_DEEP", "1.0"))


@dataclass
class TriggerDecision:
    should_investigate: bool
    move: MoveSnapshot
    reason: str
    depth: str  # light | standard | deep
    asset_class: str
    realized_vol_pct: Optional[float]
    move_zscore: Optional[float]
    benchmark_move_pct: Optional[float]
    residual_pct: Optional[float]


async def evaluate_trigger(
    ticker: str,
    *,
    window: str = "1d",
    benchmark: Optional[str] = None,
) -> TriggerDecision:
    """Decide whether a holding move warrants an investigation."""
    symbol = ticker.upper().strip()
    bench = (benchmark or BENCHMARK).upper().strip()
    move = await detect_move(symbol, window=window)
    klass = asset_class_service.classify_asset(symbol)

    history = await safe_tool("get_price_history", {"ticker": symbol, "period": "1mo"})
    realized = _realized_vol_pct(history.get("history") if history else None)
    zscore = None
    if move.move_pct is not None and realized and realized > 1e-6:
        zscore = move.move_pct / realized

    bench_move = None
    if bench and bench != symbol:
        bench_snap = await detect_move(bench, window=window)
        bench_move = bench_snap.move_pct

    residual = None
    if move.move_pct is not None and bench_move is not None:
        residual = move.move_pct - bench_move

    decision = _decide(
        move=move,
        asset_class=klass,
        realized_vol_pct=realized,
        move_zscore=zscore,
        benchmark_move_pct=bench_move,
        residual_pct=residual,
        benchmark=bench,
    )

    # Enrich move snapshot for downstream planner/runner.
    move.asset_class = klass
    move.realized_vol_pct = realized
    move.move_zscore = zscore
    move.benchmark_ticker = bench
    move.benchmark_move_pct = bench_move
    move.residual_pct = residual
    move.depth = decision.depth
    move.should_investigate = decision.should_investigate
    move.skip_reason = "" if decision.should_investigate else decision.reason
    if decision.should_investigate:
        move.is_noise = False
    else:
        move.is_noise = True
        move.detail = f"{move.detail} Trigger skip: {decision.reason}"

    log_event(
        logger,
        logging.INFO,
        "Investigation trigger evaluated",
        ticker=symbol,
        should_investigate=decision.should_investigate,
        depth=decision.depth,
        asset_class=klass,
        move_pct=move.move_pct,
        zscore=zscore,
        residual=residual,
        reason=decision.reason,
    )
    return decision


def _decide(
    *,
    move: MoveSnapshot,
    asset_class: str,
    realized_vol_pct: Optional[float],
    move_zscore: Optional[float],
    benchmark_move_pct: Optional[float],
    residual_pct: Optional[float],
    benchmark: str,
) -> TriggerDecision:
    if move.move_pct is None:
        return TriggerDecision(
            should_investigate=False,
            move=move,
            reason="Unable to resolve price move.",
            depth="light",
            asset_class=asset_class,
            realized_vol_pct=realized_vol_pct,
            move_zscore=move_zscore,
            benchmark_move_pct=benchmark_move_pct,
            residual_pct=residual_pct,
        )

    abs_move = abs(move.move_pct)
    abs_residual = abs(residual_pct) if residual_pct is not None else None
    abs_z = abs(move_zscore) if move_zscore is not None else None

    # Market/sector beta: move tracks benchmark closely.
    if abs_residual is not None and abs_residual < IDIO_PCT and abs_move < max(3.0, IDIO_PCT * 3):
        return TriggerDecision(
            should_investigate=False,
            move=move,
            reason=(
                f"Moved with {benchmark} (residual {residual_pct:+.2f}% < "
                f"{IDIO_PCT:.1f}% idiosyncratic bar)."
            ),
            depth="light",
            asset_class=asset_class,
            realized_vol_pct=realized_vol_pct,
            move_zscore=move_zscore,
            benchmark_move_pct=benchmark_move_pct,
            residual_pct=residual_pct,
        )

    # Flat absolute noise floor when we lack vol estimate.
    if abs_z is None and abs_move < float(os.getenv("INVESTIGATION_NOISE_PCT", "1.5")):
        return TriggerDecision(
            should_investigate=False,
            move=move,
            reason=f"Absolute move {abs_move:.2f}% below noise floor.",
            depth="light",
            asset_class=asset_class,
            realized_vol_pct=realized_vol_pct,
            move_zscore=move_zscore,
            benchmark_move_pct=benchmark_move_pct,
            residual_pct=residual_pct,
        )

    # Volatility-adjusted gate.
    if abs_z is not None and abs_z < VOL_Z_THRESHOLD and abs_move < 2.5:
        return TriggerDecision(
            should_investigate=False,
            move=move,
            reason=(
                f"Move z-score {abs_z:.2f} below vol threshold {VOL_Z_THRESHOLD:.1f} "
                f"(realized vol ≈ {realized_vol_pct:.2f}%)."
            ),
            depth="light",
            asset_class=asset_class,
            realized_vol_pct=realized_vol_pct,
            move_zscore=move_zscore,
            benchmark_move_pct=benchmark_move_pct,
            residual_pct=residual_pct,
        )

    depth = _depth_for_move(move.move_pct, abs_z)
    reason_bits = [f"abs move {abs_move:.2f}%"]
    if abs_z is not None:
        reason_bits.append(f"z={abs_z:.2f}")
    if abs_residual is not None:
        reason_bits.append(f"residual vs {benchmark} {residual_pct:+.2f}%")
    reason_bits.append(f"asset={asset_class}")
    reason_bits.append(f"depth={depth}")

    return TriggerDecision(
        should_investigate=True,
        move=move,
        reason="; ".join(reason_bits),
        depth=depth,
        asset_class=asset_class,
        realized_vol_pct=realized_vol_pct,
        move_zscore=move_zscore,
        benchmark_move_pct=benchmark_move_pct,
        residual_pct=residual_pct,
    )


def _depth_for_move(move_pct: float, abs_z: Optional[float]) -> str:
    # Direction-aware depth: downside investigated more aggressively.
    if move_pct < 0:
        if abs_z is not None and abs_z >= DOWNSIDE_Z_FOR_DEEP:
            return "deep"
        if abs(move_pct) >= 3.0:
            return "deep"
        return "standard"
    if abs_z is not None and abs_z >= UPSIDE_Z_FOR_DEEP:
        return "deep"
    if abs(move_pct) >= 5.0:
        return "deep"
    if abs_z is not None and abs_z >= VOL_Z_THRESHOLD:
        return "standard"
    return "light"


def _realized_vol_pct(history: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    """Daily realized volatility in percent (std of daily returns)."""
    if not history or len(history) < 6:
        return None
    closes: List[float] = []
    for row in history:
        c = as_float(row.get("close"))
        if c is not None and c > 0:
            closes.append(c)
    if len(closes) < 6:
        return None
    rets = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            rets.append((closes[i] / prev - 1.0) * 100.0)
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)

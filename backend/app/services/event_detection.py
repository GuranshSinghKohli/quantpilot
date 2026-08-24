"""Phase 2 — detect and normalize market events via MCP only."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

_LIGHTWEIGHT = os.getenv("QUANTPILOT_LIGHTWEIGHT", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

from app.config import (
    INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS,
    INVESTIGATION_EARNINGS_LOOKBACK_DAYS,
    INVESTIGATION_FILING_LOOKBACK_DAYS,
    INVESTIGATION_PRICE_MOVE_THRESHOLD,
    INVESTIGATION_VOL_Z_THRESHOLD,
)
from app.models.event_schemas import (
    EventDirection,
    EventSeverity,
    EventType,
    MarketEvent,
)
from app.observability.logger import get_logger, log_event
from app.services.move_detector import as_float, safe_tool
from app.services.trigger_logic import TriggerDecision, evaluate_trigger

logger = get_logger("event_detection")

_MATERIAL_FORMS = {"8-K", "8-K/A", "6-K", "10-K", "10-Q", "20-F"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _direction(value: Optional[float]) -> EventDirection:
    if value is None:
        return EventDirection.NONE
    if value > 0.05:
        return EventDirection.UP
    if value < -0.05:
        return EventDirection.DOWN
    return EventDirection.NONE


def _severity_from_price(
    *,
    abs_move: float,
    abs_z: Optional[float],
    abs_residual: Optional[float],
) -> EventSeverity:
    if (abs_residual is not None and abs_residual >= 5.0) or abs_move >= 6.0:
        return EventSeverity.HIGH
    if abs_z is not None and abs_z >= 2.5:
        return EventSeverity.HIGH
    if abs_move >= 3.0 or (abs_z is not None and abs_z >= 1.5):
        return EventSeverity.MEDIUM
    return EventSeverity.LOW


def events_from_trigger(decision: TriggerDecision) -> List[MarketEvent]:
    """Normalize an existing price/vol/idio trigger into MarketEvent rows."""
    move = decision.move
    symbol = move.ticker.upper().strip()
    detected = _now()
    abs_m = abs(move.move_pct) if move.move_pct is not None else 0.0
    abs_z = abs(decision.move_zscore) if decision.move_zscore is not None else None
    abs_r = abs(decision.residual_pct) if decision.residual_pct is not None else None
    severity = _severity_from_price(
        abs_move=abs_m, abs_z=abs_z, abs_residual=abs_r
    )
    direction = _direction(move.move_pct)
    bench = move.benchmark_ticker or decision.move.benchmark_ticker or None
    shared = {
        "ticker": symbol,
        "detected_at": detected,
        "occurred_at": detected,
        "direction": direction,
        "magnitude": move.move_pct,
        "benchmark": bench,
        "z_score": decision.move_zscore,
        "residual_pct": decision.residual_pct,
        "source": move.source or "mcp",
        "detection_confidence": 0.85 if move.move_pct is not None else 0.2,
        "metadata": {
            "window_label": move.window_label,
            "realized_vol_pct": decision.realized_vol_pct,
            "benchmark_move_pct": decision.benchmark_move_pct,
            "asset_class": decision.asset_class,
            "trigger_should_investigate": decision.should_investigate,
            "trigger_depth": decision.depth,
            "current_price": move.current_price,
        },
    }

    events: List[MarketEvent] = []
    if move.move_pct is not None:
        events.append(
            MarketEvent(
                event_id=f"{symbol}:PRICE_MOVE:{uuid4().hex[:10]}",
                event_type=EventType.PRICE_MOVE,
                severity=severity,
                reason=(
                    f"{symbol} {move.move_pct:+.2f}% over {move.window_label}"
                ),
                **shared,
            )
        )
        if abs_r is not None and abs_r >= 1.0:
            events.append(
                MarketEvent(
                    event_id=f"{symbol}:IDIOSYNCRATIC_MOVE:{uuid4().hex[:10]}",
                    event_type=EventType.IDIOSYNCRATIC_MOVE,
                    severity=severity,
                    reason=(
                        f"{symbol} residual vs {bench or 'benchmark'} "
                        f"{decision.residual_pct:+.2f}%"
                    ),
                    **shared,
                )
            )
        if abs_z is not None and abs_z >= INVESTIGATION_VOL_Z_THRESHOLD:
            events.append(
                MarketEvent(
                    event_id=f"{symbol}:VOLATILITY_SPIKE:{uuid4().hex[:10]}",
                    event_type=EventType.VOLATILITY_SPIKE,
                    severity=(
                        EventSeverity.HIGH if abs_z >= 2.5 else EventSeverity.MEDIUM
                    ),
                    reason=(
                        f"{symbol} move z-score {decision.move_zscore:.2f} "
                        f"(bar {INVESTIGATION_VOL_Z_THRESHOLD:.1f})"
                    ),
                    **shared,
                )
            )
    return events


def _parse_iso_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text[:19].replace("Z", ""), fmt.replace("Z", ""))
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def events_from_filings(
    ticker: str,
    payload: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    lookback_days: Optional[int] = None,
) -> List[MarketEvent]:
    """Normalize MCP get_recent_filings into FILING events (no I/O)."""
    symbol = ticker.upper().strip()
    if not payload:
        return []
    detected = now or _now()
    lookback = lookback_days if lookback_days is not None else INVESTIGATION_FILING_LOOKBACK_DAYS
    cutoff = detected - timedelta(days=max(1, lookback))
    events: List[MarketEvent] = []
    for raw in payload.get("filings") or payload.get("items") or []:
        form = str(raw.get("form_type") or raw.get("form") or "").upper().strip()
        if form not in _MATERIAL_FORMS:
            continue
        occurred = _parse_iso_date(raw.get("filing_date") or raw.get("date"))
        if occurred is None or occurred < cutoff:
            continue
        high = form.startswith("8-K") or form == "6-K"
        events.append(
            MarketEvent(
                event_id=f"{symbol}:FILING:{form}:{occurred.date().isoformat()}",
                ticker=symbol,
                event_type=EventType.FILING,
                detected_at=detected,
                occurred_at=occurred,
                direction=EventDirection.NONE,
                magnitude=None,
                severity=EventSeverity.HIGH if high else EventSeverity.MEDIUM,
                source="mcp:get_recent_filings",
                reason=f"{symbol} filed {form} on {occurred.date().isoformat()}",
                detection_confidence=0.9,
                metadata={
                    "filing_type": form,
                    "accession_number": raw.get("accession_number"),
                    "document_url": raw.get("document_url") or raw.get("url"),
                    "report_date": raw.get("report_date"),
                },
            )
        )
    return events


def events_from_fundamentals(
    ticker: str,
    payload: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    lookahead_days: Optional[int] = None,
    lookback_days: Optional[int] = None,
) -> List[MarketEvent]:
    """Normalize MCP fundamentals earnings dates into EARNINGS events (no I/O)."""
    symbol = ticker.upper().strip()
    if not payload:
        return []
    detected = now or _now()
    ahead = (
        lookahead_days
        if lookahead_days is not None
        else INVESTIGATION_EARNINGS_LOOKAHEAD_DAYS
    )
    back = (
        lookback_days
        if lookback_days is not None
        else INVESTIGATION_EARNINGS_LOOKBACK_DAYS
    )
    stamp = (
        payload.get("earnings_date")
        or payload.get("earnings_timestamp")
        or payload.get("next_earnings_date")
    )
    occurred = _parse_iso_date(stamp)
    if occurred is None:
        return []
    start = detected - timedelta(days=max(0, back))
    end = detected + timedelta(days=max(0, ahead))
    if occurred < start or occurred > end:
        return []
    upcoming = occurred >= detected
    surprise = as_float(payload.get("earnings_surprise_pct"))
    return [
        MarketEvent(
            event_id=f"{symbol}:EARNINGS:{occurred.date().isoformat()}",
            ticker=symbol,
            event_type=EventType.EARNINGS,
            detected_at=detected,
            occurred_at=occurred,
            direction=_direction(surprise),
            magnitude=surprise,
            severity=EventSeverity.HIGH,
            source="mcp:get_stock_fundamentals",
            reason=(
                f"{symbol} earnings "
                f"{'upcoming' if upcoming else 'recently reported'} "
                f"{occurred.date().isoformat()}"
            ),
            detection_confidence=0.8 if stamp else 0.5,
            metadata={
                "earnings_date": occurred.isoformat(),
                "upcoming": upcoming,
                "surprise_pct": surprise,
            },
        )
    ]


async def detect_filing_events(ticker: str) -> List[MarketEvent]:
    payload = await safe_tool("get_recent_filings", {"ticker": ticker, "limit": 5})
    events = events_from_filings(ticker, payload)
    for ev in events:
        log_event(
            logger,
            logging.INFO,
            "EVENT_DETECTED",
            event="EVENT_DETECTED",
            ticker=ev.ticker,
            event_type=ev.event_type.value,
            severity=ev.severity.value,
            reason=ev.reason,
        )
    return events


async def detect_earnings_events(ticker: str) -> List[MarketEvent]:
    payload = await safe_tool("get_stock_fundamentals", {"ticker": ticker})
    events = events_from_fundamentals(ticker, payload)
    for ev in events:
        log_event(
            logger,
            logging.INFO,
            "EVENT_DETECTED",
            event="EVENT_DETECTED",
            ticker=ev.ticker,
            event_type=ev.event_type.value,
            severity=ev.severity.value,
            reason=ev.reason,
        )
    return events


async def detect_events(
    ticker: str,
    *,
    window: str = "1d",
    benchmark: Optional[str] = None,
    trigger: Optional[TriggerDecision] = None,
) -> List[MarketEvent]:
    """Price/vol/idio (existing trigger) plus filing and earnings via MCP."""
    symbol = ticker.upper().strip()
    decision = trigger or await evaluate_trigger(symbol, window=window, benchmark=benchmark)
    events = events_from_trigger(decision)
    for ev in events:
        if ev.event_type == EventType.PRICE_MOVE and ev.magnitude is not None:
            if abs(ev.magnitude) < INVESTIGATION_PRICE_MOVE_THRESHOLD and not (
                ev.z_score is not None
                and abs(ev.z_score) >= INVESTIGATION_VOL_Z_THRESHOLD
            ):
                log_event(
                    logger,
                    logging.INFO,
                    "EVENT_REJECTED",
                    event="EVENT_REJECTED",
                    ticker=symbol,
                    event_type=ev.event_type.value,
                    reason="below price-move / vol threshold",
                    magnitude=ev.magnitude,
                )
        log_event(
            logger,
            logging.INFO,
            "EVENT_DETECTED",
            event="EVENT_DETECTED",
            ticker=ev.ticker,
            event_type=ev.event_type.value,
            magnitude=ev.magnitude,
            residual_pct=ev.residual_pct,
            z_score=ev.z_score,
            reason=ev.reason,
        )
    if not _LIGHTWEIGHT:
        events.extend(await detect_filing_events(symbol))
        events.extend(await detect_earnings_events(symbol))
    return events

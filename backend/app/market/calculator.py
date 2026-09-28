"""Deterministic event-window analytics. Independent of FastAPI."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.pipeline_context import pipeline_bind
from app.core.settings import get_settings
from app.domain.enums import EventStatus
from app.domain.models import Entity, Event, EventEntity, EventImpact
from app.market import (
    CALCULATION_VERSION,
    EVENT_WINDOW_SESSIONS,
    VOLATILITY_LOOKBACK,
    VOLUME_LOOKBACK,
)
from app.market.calendar import (
    TradingCalendar,
    daily_returns,
    event_timestamp,
    load_sessions,
    sample_stdev,
    simple_return,
)
from app.market.metrics import MARKET_METRICS
from app.observability.logger import get_logger, log_event

logger = get_logger("market")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def entity_symbol(entity: Entity) -> Optional[str]:
    company = entity.company
    if company is None:
        return None
    if company.ticker:
        return company.ticker.upper()
    if company.tickers:
        first = str(company.tickers[0]).upper().strip()
        return first or None
    return None


def _relative(asset: Optional[float], bench: Optional[float]) -> Optional[float]:
    if asset is None or bench is None:
        return None
    return asset - bench


@dataclass
class ImpactResult:
    impact: Optional[EventImpact] = None
    result: str = "unavailable"
    created: bool = False
    unavailable: List[str] = field(default_factory=list)


class EventImpactCalculator:
    """Locate T0, compute session windows, persist one row per event/entity/symbol/version."""

    def __init__(self, *, calculation_version: str = CALCULATION_VERSION, benchmark_symbol: Optional[str] = None):
        self.calculation_version = calculation_version
        self.benchmark_symbol = (benchmark_symbol or get_settings().MARKET_BENCHMARK_SYMBOL).upper()

    def calculate_event(
        self,
        db: Session,
        event_id: uuid.UUID,
        *,
        job_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, object]:
        started = time.perf_counter()
        event = db.get(Event, event_id)
        if event is None:
            return {"event_id": str(event_id), "result": "failed", "reason": "event_not_found", "impacts": []}
        if event.status != EventStatus.CONFIRMED.value:
            return {
                "event_id": str(event_id),
                "result": "skipped",
                "reason": "event_not_confirmed",
                "impacts": [],
            }
        links = list(
            db.scalars(
                select(EventEntity)
                .options(selectinload(EventEntity.entity).selectinload(Entity.company))
                .where(EventEntity.event_id == event.id)
            )
        )
        impacts: List[ImpactResult] = []
        for link in links:
            impacts.append(self._calculate_link(db, event, link, job_id=job_id))
        duration = time.perf_counter() - started
        MARKET_METRICS.observe(
            "market_impact_duration_seconds",
            duration,
            operation="single",
            calculation_version=self.calculation_version,
        )
        result = "ok" if impacts and all(item.result in {"inserted", "updated"} for item in impacts) else "partial"
        if not impacts:
            result = "unavailable"
        MARKET_METRICS.inc(
            "market_impact_calculations_total",
            result=result,
            operation="single",
            calculation_version=self.calculation_version,
        )
        log_event(
            logger,
            20,
            "MARKET_IMPACT_CALCULATED",
            component="market",
            operation="impact",
            event_type=event.event_type,
            status=result,
            calculation_version=self.calculation_version,
            duration_ms=int(duration * 1000),
        )
        return {
            "event_id": str(event.id),
            "result": result,
            "impacts": [
                {
                    "impact_id": str(item.impact.id) if item.impact else None,
                    "result": item.result,
                    "unavailable": item.unavailable,
                    "created": item.created,
                }
                for item in impacts
            ],
            "duration_seconds": round(duration, 3),
        }

    def calculate_batch(
        self,
        db: Session,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        job_id: Optional[uuid.UUID] = None,
    ) -> dict:
        started = time.perf_counter()
        job = job_id or uuid.uuid4()
        inserted = updated = skipped = unavailable = failed = 0
        with pipeline_bind(component="market", operation="impact-batch"):
            events = list(db.scalars(select(Event).where(Event.status == EventStatus.CONFIRMED.value)))
            for event in events:
                ts = event_timestamp(occurred_at=event.occurred_at, published_at=event.published_at)
                if since and (ts is None or ts < since):
                    skipped += 1
                    continue
                if until and (ts is None or ts > until):
                    skipped += 1
                    continue
                try:
                    report = self.calculate_event(db, event.id, job_id=job)
                    if report["result"] == "skipped":
                        skipped += 1
                        continue
                    if not report["impacts"]:
                        unavailable += 1
                        continue
                    for item in report["impacts"]:
                        if item["result"] == "inserted":
                            inserted += 1
                        elif item["result"] == "updated":
                            updated += 1
                        elif item["result"] == "unavailable":
                            unavailable += 1
                        elif item["result"] == "failed":
                            failed += 1
                        else:
                            skipped += 1
                except Exception:
                    failed += 1
                    MARKET_METRICS.inc(
                        "market_impact_calculation_failures_total",
                        result="failed",
                        calculation_version=self.calculation_version,
                        operation="batch",
                    )
                    log_event(
                        logger,
                        40,
                        "MARKET_IMPACT_FAILED",
                        component="market",
                        operation="impact-batch",
                        event_type=event.event_type,
                        status="failed",
                        calculation_version=self.calculation_version,
                    )
        duration = time.perf_counter() - started
        MARKET_METRICS.observe(
            "market_impact_duration_seconds",
            duration,
            operation="batch",
            calculation_version=self.calculation_version,
        )
        MARKET_METRICS.inc(
            "market_impact_calculations_total",
            result="ok" if failed == 0 else "partial",
            operation="batch",
            calculation_version=self.calculation_version,
        )
        log_event(
            logger,
            20,
            "MARKET_IMPACT_BATCH",
            component="market",
            operation="impact-batch",
            status="ok" if failed == 0 else "partial",
            calculation_version=self.calculation_version,
            duration_ms=int(duration * 1000),
        )
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "unavailable": unavailable,
            "failed": failed,
            "calculation_job_id": str(job),
            "calculation_version": self.calculation_version,
            "duration_seconds": round(duration, 3),
        }

    def _calculate_link(
        self,
        db: Session,
        event: Event,
        link: EventEntity,
        *,
        job_id: Optional[uuid.UUID],
    ) -> ImpactResult:
        entity = link.entity or db.get(Entity, link.entity_id)
        if entity is not None and entity.company is None:
            db.refresh(entity)
        symbol = entity_symbol(entity) if entity is not None else None
        if not symbol:
            MARKET_METRICS.inc(
                "market_impact_unavailable_total",
                result="no_symbol",
                calculation_version=self.calculation_version,
                operation="single",
            )
            return ImpactResult(result="unavailable", unavailable=["symbol"])

        calendar = TradingCalendar(load_sessions(db, symbol))
        bench = TradingCalendar(load_sessions(db, self.benchmark_symbol))
        ts = event_timestamp(occurred_at=event.occurred_at, published_at=event.published_at)
        unavailable: List[str] = []
        t0 = calendar.event_trading_date(ts) if ts else None
        if t0 is None:
            unavailable.append("event_trading_date")
        baseline = calendar.get(t0).close if t0 and calendar.get(t0) else None
        if baseline is None:
            unavailable.append("baseline_price")

        values = self._window_values(calendar, t0, baseline)
        bench_values = self._window_values(bench, t0, bench.get(t0).close if t0 and bench.get(t0) else None)
        if t0 and bench.get(t0) is None:
            unavailable.append("benchmark")
            bench_values = {key: None for key in bench_values}

        volume = self._volume(calendar, t0)
        vol = self._volatility(calendar, t0)
        if volume["volume_ratio"] is None:
            unavailable.append("volume")
        if vol["pre_event_volatility"] is None:
            unavailable.append("pre_event_volatility")
        if vol["event_window_volatility"] is None:
            unavailable.append("event_window_volatility")

        for key, value in values.items():
            if value is None:
                unavailable.append(key)
        for key, value in bench_values.items():
            if value is None:
                unavailable.append(f"benchmark_{key}")

        dates = calendar.dates()
        payload = {
            "event_id": event.id,
            "entity_id": link.entity_id,
            "symbol": symbol,
            "event_trading_date": t0,
            "baseline_price": float(baseline) if baseline is not None else None,
            "return_m1": values.get("return_m1"),
            "return_p1": values.get("return_p1"),
            "return_p3": values.get("return_p3"),
            "return_p5": values.get("return_p5"),
            "return_p20": values.get("return_p20"),
            "benchmark_return_p1": bench_values.get("return_p1"),
            "benchmark_return_p3": bench_values.get("return_p3"),
            "benchmark_return_p5": bench_values.get("return_p5"),
            "benchmark_return_p20": bench_values.get("return_p20"),
            "relative_return_p1": _relative(values.get("return_p1"), bench_values.get("return_p1")),
            "relative_return_p3": _relative(values.get("return_p3"), bench_values.get("return_p3")),
            "relative_return_p5": _relative(values.get("return_p5"), bench_values.get("return_p5")),
            "relative_return_p20": _relative(values.get("return_p20"), bench_values.get("return_p20")),
            "event_day_volume": volume["event_day_volume"],
            "pre_event_average_volume": volume["pre_event_average_volume"],
            "volume_ratio": volume["volume_ratio"],
            "pre_event_volatility": vol["pre_event_volatility"],
            "event_window_volatility": vol["event_window_volatility"],
            "first_market_date": dates[0] if dates else None,
            "last_market_date": dates[-1] if dates else None,
            "calculation_version": self.calculation_version,
            "calculation_job_id": job_id,
            "calculated_at": _now(),
        }
        row, created = self._upsert(db, payload)
        if unavailable:
            MARKET_METRICS.inc(
                "market_impact_unavailable_total",
                result="partial" if t0 is not None else "unavailable",
                calculation_version=self.calculation_version,
                operation="single",
            )
        return ImpactResult(
            impact=row,
            result="inserted" if created else "updated",
            created=created,
            unavailable=sorted(set(unavailable)),
        )

    def _window_values(
        self,
        calendar: TradingCalendar,
        t0: Optional[date],
        baseline: Optional[Decimal],
    ) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {
            "return_m1": None,
            "return_p1": None,
            "return_p3": None,
            "return_p5": None,
            "return_p20": None,
        }
        if t0 is None or baseline is None:
            return out
        prior = calendar.offset(t0, -1)
        if prior is not None:
            out["return_m1"] = simple_return(prior.close, baseline)
        for key, n in (("return_p1", 1), ("return_p3", 3), ("return_p5", 5), ("return_p20", 20)):
            later = calendar.offset(t0, n)
            out[key] = simple_return(baseline, later.close) if later is not None else None
        return out

    def _volume(self, calendar: TradingCalendar, t0: Optional[date]) -> Dict[str, Optional[float]]:
        empty = {"event_day_volume": None, "pre_event_average_volume": None, "volume_ratio": None}
        if t0 is None:
            return empty
        event_bar = calendar.get(t0)
        if event_bar is None:
            return empty
        prior = calendar.prior(t0, VOLUME_LOOKBACK)
        event_vol = float(event_bar.volume)
        if len(prior) < VOLUME_LOOKBACK:
            return {"event_day_volume": event_vol, "pre_event_average_volume": None, "volume_ratio": None}
        average = sum(bar.volume for bar in prior) / len(prior)
        if average <= 0:
            return {"event_day_volume": event_vol, "pre_event_average_volume": float(average), "volume_ratio": None}
        return {
            "event_day_volume": event_vol,
            "pre_event_average_volume": float(average),
            "volume_ratio": event_vol / average,
        }

    def _volatility(self, calendar: TradingCalendar, t0: Optional[date]) -> Dict[str, Optional[float]]:
        empty = {"pre_event_volatility": None, "event_window_volatility": None}
        if t0 is None:
            return empty
        prior = calendar.prior(t0, VOLATILITY_LOOKBACK)
        pre = None
        if len(prior) >= VOLATILITY_LOOKBACK:
            pre_returns = daily_returns([bar.close for bar in prior])
            pre = sample_stdev(pre_returns) if pre_returns else None
        window = calendar.window(t0, 0, EVENT_WINDOW_SESSIONS)
        event_vol = None
        if window is not None:
            event_returns = daily_returns([bar.close for bar in window])
            event_vol = sample_stdev(event_returns) if event_returns else None
        return {"pre_event_volatility": pre, "event_window_volatility": event_vol}

    def _upsert(self, db: Session, payload: dict) -> tuple[EventImpact, bool]:
        existing = db.scalars(
            select(EventImpact).where(
                EventImpact.event_id == payload["event_id"],
                EventImpact.entity_id == payload["entity_id"],
                EventImpact.symbol == payload["symbol"],
                EventImpact.calculation_version == payload["calculation_version"],
            )
        ).first()
        if existing is None:
            row = EventImpact(id=uuid.uuid4(), **payload)
            db.add(row)
            db.flush()
            return row, True
        for key, value in payload.items():
            setattr(existing, key, value)
        db.flush()
        return existing, False

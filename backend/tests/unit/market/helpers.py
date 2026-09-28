"""Shared fixtures for event/market intelligence tests. SYNTHETIC data only."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import EventEntityRole, EventStatus, EventType, SourceType
from app.domain.ingestion_schemas import MarketBarIn, NewsArticleIn
from app.domain.models import Event
from app.domain.schemas import CompanyIn, EventEntityIn, EventIn, EvidenceIn, SourceIn
from app.repositories import entities, events, sources
from app.repositories.canonical import upsert_market_bars, upsert_news
from tests.unit.pipeline.test_ingestion_repositories import Env, _hex

NOW = datetime(2026, 1, 16, 15, 0, tzinfo=timezone.utc)
T0 = date(2026, 1, 16)


def migrated_db(tmp_path: Path) -> Session:
    engine = build_engine(f"sqlite:///{tmp_path / 'market.db'}")
    upgrade_to_head(engine)
    session = sessionmaker(bind=engine)()
    session.info["engine"] = engine
    return session


def close_db(session: Session) -> None:
    engine = session.info.get("engine")
    session.close()
    if engine is not None:
        engine.dispose()


def company(db: Session, name: str, ticker: str, cik: str = "1"):
    row, _ = entities.get_or_create_company(db, CompanyIn(legal_name=name, ticker=ticker, cik=cik.zfill(10)))
    return row


def source(db: Session, url: str = "https://example.com/news/nvda", text: str = "NVIDIA announced a partnership."):
    row, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url=url,
            retrieved_at=NOW,
            content_text=text,
            title="NVIDIA partnership",
            published_at=NOW,
        ),
    )
    return row


def evidence(db: Session, source_id, excerpt: str = "NVIDIA announced a partnership."):
    row, _ = sources.add_evidence(
        db, source_id, EvidenceIn(excerpt=excerpt, extraction_method="structured", extracted_at=NOW)
    )
    return row


def confirmed_event(db: Session, entity_id, *, event_type=EventType.PARTNERSHIP, occurred_at=NOW, title="Partnership"):
    src = source(db, url=f"https://example.com/events/{uuid.uuid4()}")
    ev = evidence(db, src.id, title)
    event, _ = events.upsert_event(
        db,
        EventIn(
            event_type=event_type,
            title=title,
            detected_at=NOW,
            occurred_at=occurred_at,
            published_at=NOW,
            primary_source_id=src.id,
            evidence_id=ev.id,
            entities=[EventEntityIn(entity_id=entity_id, role=EventEntityRole.SUBJECT, source_id=src.id, evidence_id=ev.id)],
        ),
    )
    event.status = EventStatus.CONFIRMED.value
    db.flush()
    return event


def _bar(
    env: Env,
    n: int,
    *,
    symbol: str,
    day: date,
    close: str,
    volume: int = 1000,
    open_: str | None = None,
    high: str | None = None,
    low: str | None = None,
) -> MarketBarIn:
    close_d = Decimal(close)
    return MarketBarIn(
        source_ingested_at=NOW,
        event_id=env.event(n),
        processing_job_id=env.job.id,
        raw_uri=f"s3://lake/raw/market/{n}.json",
        content_hash=_hex(40_000 + n),
        symbol=symbol,
        interval="1d",
        trading_date=day,
        bar_ts=datetime.combine(day, datetime.min.time(), timezone.utc),
        open=Decimal(open_ or close),
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=close_d,
        volume=volume,
        source="massive",
    )


def seed_sessions(db: Session, symbol: str, rows: list[tuple[date, str, int]], start_n: int = 1) -> None:
    env = Env(db, "massive.market_daily")
    upsert_market_bars(db, [_bar(env, start_n + i, symbol=symbol, day=day, close=close, volume=vol) for i, (day, close, vol) in enumerate(rows)])


def weekdays(start: date, count: int, skip: set[date] | None = None) -> list[date]:
    skip = skip or set()
    out = []
    day = start
    while len(out) < count:
        if day.weekday() < 5 and day not in skip:
            out.append(day)
        day += timedelta(days=1)
    return out


def seed_news(db: Session, *, tickers: list[str], external_id: str = "news-1", url: str = "https://example.com/a") -> None:
    env = Env(db, "massive.news")
    upsert_news(
        db,
        [
            NewsArticleIn(
                source_ingested_at=NOW,
                event_id=env.event(1),
                processing_job_id=env.job.id,
                raw_uri="s3://lake/raw/news/1.json",
                content_hash=_hex(50_000),
                source_key="massive.news",
                external_id=external_id,
                url=url,
                title="Vendor headline",
                publisher="Example Wire",
                published_at=NOW,
                summary="NVIDIA and ACME were mentioned.",
                tickers=tickers,
            )
        ],
    )

"""Shared fixtures for retrieval unit tests. SYNTHETIC data only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head
from app.domain.enums import SourceType
from app.domain.schemas import CompanyIn, SourceIn
from app.repositories import entities, sources

NOW = datetime(2026, 4, 1, 15, 0, tzinfo=timezone.utc)


def migrated_db(tmp_path: Path) -> Session:
    engine = build_engine(f"sqlite:///{tmp_path / 'retrieval.db'}")
    upgrade_to_head(engine)
    session = sessionmaker(bind=engine)()
    session.info["engine"] = engine
    return session


def close_db(session: Session) -> None:
    engine = session.info.get("engine")
    session.close()
    if engine is not None:
        engine.dispose()


def company(db: Session, name: str, ticker: str, cik: str):
    row, _ = entities.get_or_create_company(db, CompanyIn(legal_name=name, ticker=ticker, cik=cik.zfill(10)))
    return row


def news_source(db: Session, url: str, title: str, summary: str, tickers=None):
    row, _ = sources.upsert_source(
        db,
        SourceIn(
            source_type=SourceType.NEWS,
            url=url,
            retrieved_at=NOW,
            content_text=summary,
            title=title,
            published_at=NOW,
            metadata={"summary": summary, "tickers": list(tickers or [])},
        ),
    )
    return row

"""SQLAlchemy engine, session factory, and request-scoped session dependency."""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


def build_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
    built = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.startswith("postgresql"):
        from sqlalchemy import event

        @event.listens_for(built, "connect")
        def _register_pgvector(dbapi_conn, _connection_record) -> None:
            try:
                from pgvector.psycopg2 import register_vector

                register_vector(dbapi_conn)
            except Exception:
                # Extension may not be installed yet (pre-0011) or the extra is missing.
                return

    return built


engine = build_engine(get_settings().DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

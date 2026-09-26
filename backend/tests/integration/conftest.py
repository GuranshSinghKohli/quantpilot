"""PostgreSQL fixtures for the integration tier.

Set TEST_DATABASE_URL to a disposable database (its public schema is wiped at
session start). Without it every test here is skipped.
"""

from __future__ import annotations

import os
from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.db import build_engine
from app.core.migrations import upgrade_to_head

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL not set")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.integration)
            if not TEST_DATABASE_URL:
                item.add_marker(skip)


def reset_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point at PostgreSQL")
    engine = build_engine(TEST_DATABASE_URL)
    reset_schema(engine)
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(pg_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after each test, including commits
    made inside nested transactions (SAVEPOINTs)."""
    conn = pg_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()

"""A database built by the old create_all startup path is adopted, not rebuilt."""

from __future__ import annotations

from alembic import command
from sqlalchemy import inspect, text

from app.core.db import build_engine
from app.core.migrations import alembic_config, head_revision, migration_state, upgrade_to_head


def _pre_alembic_db(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), "0001")
        # What an older create_all database looked like: no version table, a table
        # and an index that were added later, and a column the old ALTER step added.
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("DROP TABLE company_memory_items"))
        conn.execute(text("DROP INDEX ix_investigations_owner_ticker_created"))
        conn.execute(text("ALTER TABLE claims DROP COLUMN claim_type"))
        conn.execute(text("INSERT INTO users (email, hashed_password, created_at) VALUES ('a@b.c', 'x', CURRENT_TIMESTAMP)"))
    return engine


def test_legacy_database_is_adopted_and_upgraded_without_data_loss(tmp_path):
    engine = _pre_alembic_db(tmp_path)
    try:
        state = upgrade_to_head(engine)
        assert state.is_current and state.current == head_revision()

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"company_memory_items", "sources", "research_jobs"} <= tables
        assert not {"briefings", "alert_rules"} & tables
        assert "claim_type" in {c["name"] for c in inspector.get_columns("claims")}
        assert "ix_investigations_owner_ticker_created" in {
            ix["name"] for ix in inspector.get_indexes("investigations")
        }
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM users")).scalar() == 1
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        upgrade_to_head(engine)
        upgrade_to_head(engine)
        assert migration_state(engine).is_current
    finally:
        engine.dispose()

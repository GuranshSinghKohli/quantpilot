"""Alembic chain on PostgreSQL: head matches the models, and it round-trips."""

from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

import app.domain.models  # noqa: F401  (registers core tables)
from app.core.migrations import alembic_config, head_revision, migration_state, upgrade_to_head
from app.db.models import Base

CORE_TABLES = {
    "entities",
    "companies",
    "technologies",
    "sources",
    "evidence_records",
    "events",
    "event_entities",
    "relationships",
    "relationship_evidence",
    "market_event_windows",
    "research_jobs",
    "agent_runs",
    "research_claims",
    "claim_evidence",
    "reports",
    "retrieval_documents",
    "retrieval_chunks",
    "chunk_embeddings",
    "document_entities",
}


def test_head_has_no_drift_from_models(pg_engine):
    with pg_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_head_contains_core_tables_and_not_dropped_legacy(pg_engine):
    tables = set(inspect(pg_engine).get_table_names())
    assert CORE_TABLES <= tables
    assert not {"briefings", "alert_rules"} & tables
    assert "rule_id" not in {c["name"] for c in inspect(pg_engine).get_columns("alert_events")}


def test_downgrade_to_base_and_back(pg_engine):
    with pg_engine.begin() as conn:
        command.downgrade(alembic_config(conn), "base")
    assert set(inspect(pg_engine).get_table_names()) <= {"alembic_version"}

    state = upgrade_to_head(pg_engine)
    assert state.is_current
    assert migration_state(pg_engine).current == head_revision()
    assert CORE_TABLES <= set(inspect(pg_engine).get_table_names())


def test_pgvector_extension_is_enabled(pg_engine):
    """Phase 4.1 enables the extension; the image must still ship it."""
    with pg_engine.connect() as conn:
        available = conn.exec_driver_sql(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        ).first()
        installed = conn.exec_driver_sql(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).first()
    assert available is not None
    assert installed is not None

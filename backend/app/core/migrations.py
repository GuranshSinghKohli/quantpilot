"""Programmatic Alembic entry points: upgrade, adopt pre-Alembic databases, report state."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.core.settings import BACKEND_DIR
from app.core.structured_logging import get_logger, log_event

logger = get_logger("migrations")

ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
BASELINE_REVISION = "0001"

# Baseline tables that still exist at head (briefings and alert_rules are dropped by 0002).
_LEGACY_TABLES = (
    "users",
    "portfolios",
    "holdings",
    "alert_events",
    "analysis_runs",
    "investigations",
    "claims",
    "evidence_items",
    "claim_evidence_links",
    "company_memory_items",
)

# Columns that the pre-Alembic startup code added with ALTER TABLE. A database
# created before those patches must receive them before it can be stamped at
# the baseline revision.
_PRE_ALEMBIC_COLUMNS: Dict[str, Dict[str, str]] = {
    "holdings": {
        "shares": "FLOAT",
        "avg_cost": "FLOAT",
        "source": "VARCHAR(32) DEFAULT 'manual'",
    },
    "investigations": {
        "verification_notes": "TEXT DEFAULT ''",
        "da_outcome_json": "TEXT DEFAULT '{}'",
        "roster_json": "TEXT DEFAULT '{}'",
    },
    "alert_events": {
        "investigation_id": "INTEGER",
        "materiality_score": "FLOAT",
        "feedback": "VARCHAR(32) DEFAULT ''",
    },
    "evidence_items": {"content_hash": "VARCHAR(64) DEFAULT ''"},
    "claims": {"claim_type": "VARCHAR(32) DEFAULT 'CAUSAL_EXPLANATION'"},
}


@dataclass(frozen=True)
class MigrationState:
    current: Optional[str]
    head: str

    @property
    def is_current(self) -> bool:
        return self.current == self.head


def alembic_config(connection: Optional[Connection] = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["configure_logger"] = False
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


@lru_cache(maxsize=1)
def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("No Alembic revisions found")
    return head


def migration_state(engine: Engine) -> MigrationState:
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return MigrationState(current=current, head=head_revision())


def _create_missing_legacy_objects(conn: Connection, tables: set) -> None:
    """Old startup ran create_all every boot: missing tables appeared, but indexes
    added to existing tables never did. Reproduce the tables and backfill indexes."""
    from app.db.models import Base  # app.db imports this module at package init

    legacy = [Base.metadata.tables[name] for name in _LEGACY_TABLES]
    missing = [t for t in legacy if t.name not in tables]
    if missing:
        Base.metadata.create_all(conn, tables=missing)
        log_event(logger, logging.INFO, "Created missing legacy tables", tables=[t.name for t in missing])
    inspector = inspect(conn)
    for table in legacy:
        if table in missing:
            continue
        present = {ix["name"] for ix in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name not in present:
                index.create(conn)
                log_event(logger, logging.INFO, "Created missing legacy index", index=index.name)


def _adopt_pre_alembic_database(conn: Connection) -> bool:
    """Stamp a database built by the old create_all path at the baseline revision.

    Known residual drift on adopted databases: columns that were added by ALTER
    stay nullable, and on SQLite the alert_events.investigation_id foreign key
    is absent. Neither changes application behavior.
    """
    tables = set(inspect(conn).get_table_names())
    if "alembic_version" in tables or "users" not in tables:
        return False
    inspector = inspect(conn)
    for table, columns in _PRE_ALEMBIC_COLUMNS.items():
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                log_event(logger, logging.INFO, "Added pre-Alembic column", table=table, column=column)
    _create_missing_legacy_objects(conn, tables)
    command.stamp(alembic_config(conn), BASELINE_REVISION)
    log_event(logger, logging.INFO, "Adopted pre-Alembic database", revision=BASELINE_REVISION)
    return True


def upgrade_to_head(engine: Engine) -> MigrationState:
    with engine.begin() as conn:
        _adopt_pre_alembic_database(conn)
        command.upgrade(alembic_config(conn), "head")
    state = migration_state(engine)
    log_event(
        logger,
        logging.INFO,
        "Database migrated",
        dialect=engine.dialect.name,
        revision=state.current,
    )
    return state

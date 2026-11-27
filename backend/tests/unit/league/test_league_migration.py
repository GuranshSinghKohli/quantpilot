"""League migrations against local Compose Postgres. Refuses any other host.

The default URL is the Compose mapping in docker-compose.yml (localhost:5432).
Set LEAGUE_TEST_DATABASE_URL when that port is held by a different database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.league.local_url import assert_localhost_database_url
from app.league.models import LeagueBase

INI = Path(__file__).resolve().parents[3] / "alembic_league.ini"
DEFAULT_URL = "postgresql://quantpilot:quantpilot@127.0.0.1:5432/quantpilot"
LEAGUE_TABLES = {
    "players",
    "leagues",
    "seasons",
    "league_members",
    "agents",
    "agent_config_history",
    "portfolios",
    "positions",
    "orders",
    "fills",
    "rationales",
    "agent_cycles",
    "equity_snapshots",
    "achievements",
    "user_achievements",
    "bars",
}


def _url() -> str:
    return os.environ.get("LEAGUE_TEST_DATABASE_URL", DEFAULT_URL)


def _config(url: str) -> Config:
    cfg = Config(str(INI))
    cfg.attributes["configure_logger"] = False
    cfg.attributes["database_url"] = assert_localhost_database_url(url)
    return cfg


def _engine(url: str) -> Engine:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"local Compose Postgres is not reachable: {exc}")
    return engine


def _fingerprint(engine: Engine) -> dict:
    with engine.connect() as conn:
        columns = conn.execute(
            text(
                """
                SELECT table_name, ordinal_position, column_name, udt_name,
                       is_nullable, column_default, character_maximum_length,
                       numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
        ).all()
        constraints = conn.execute(
            text(
                """
                SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                ORDER BY 1, 2
                """
            )
        ).all()
        indexes = conn.execute(
            text(
                """
                SELECT tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                ORDER BY 1, 2
                """
            )
        ).all()
        tables = conn.execute(
            text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY 1
                """
            )
        ).scalars().all()
        version = conn.execute(
            text("SELECT version_num FROM public.alembic_version ORDER BY 1")
        ).scalars().all()
    return {
        "tables": tuple(tables),
        "columns": tuple(columns),
        "constraints": tuple(constraints),
        "indexes": tuple(indexes),
        "alembic_version": tuple(version),
    }


def _columns_for(fingerprint: dict, table: str) -> list[str]:
    lines = []
    for row in fingerprint["columns"]:
        if row[0] != table:
            continue
        nullable = "NULL" if row[4] == "YES" else "NOT NULL"
        default = f" default {row[5]}" if row[5] else ""
        lines.append(f"{row[2]} {row[3]} {nullable}{default}")
    return lines


def _league_tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'league' AND c.relkind = 'r'
                ORDER BY 1
                """
            )
        ).scalars().all()
    return set(rows)


def _print_public(label: str, fingerprint: dict) -> None:
    print(f"\n== {label} ==")
    print(f"public tables: {len(fingerprint['tables'])}")
    print(f"public.alembic_version: {list(fingerprint['alembic_version'])}")
    print("public.users columns:")
    for line in _columns_for(fingerprint, "users"):
        print(f"  {line}")
    print("public.portfolios columns:")
    for line in _columns_for(fingerprint, "portfolios"):
        print(f"  {line}")


def test_metadata_contains_only_league_tables() -> None:
    schemas = {table.schema for table in LeagueBase.metadata.tables.values()}
    names = {table.name for table in LeagueBase.metadata.tables.values()}
    assert schemas == {"league"}
    assert names == LEAGUE_TABLES
    assert "users" not in names


def test_public_tables_unchanged_across_upgrade_downgrade_upgrade() -> None:
    url = _url()
    assert_localhost_database_url(url)
    engine = _engine(url)
    cfg = _config(url)
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("SHOW server_version")).scalar()
            database = conn.execute(text("SELECT current_database()")).scalar()
        print(f"\nserver_version: {server_version}")
        print(f"database: {database}")
        assert str(server_version).startswith("16."), server_version
        assert database == "quantpilot"

        before = _fingerprint(engine)
        _print_public("before", before)
        assert _columns_for(before, "users")
        assert _columns_for(before, "portfolios")

        command.upgrade(cfg, "head")
        upgraded = _fingerprint(engine)
        _print_public("after upgrade", upgraded)
        assert upgraded == before
        assert _league_tables(engine) == LEAGUE_TABLES | {"alembic_version"}
        with engine.connect() as conn:
            indexdef = conn.execute(
                text(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'league'
                      AND indexname = 'uq_user_achievements_player_achievement_season'
                    """
                )
            ).scalar()
        print(f"user_achievements unique index: {indexdef}")
        assert indexdef is not None
        assert "NULLS NOT DISTINCT" in indexdef

        command.downgrade(cfg, "base")
        downgraded = _fingerprint(engine)
        _print_public("after downgrade base", downgraded)
        assert downgraded == before
        assert _league_tables(engine) == {"alembic_version"}

        command.upgrade(cfg, "head")
        again = _fingerprint(engine)
        _print_public("after upgrade again", again)
        assert again == before
        assert _league_tables(engine) == LEAGUE_TABLES | {"alembic_version"}
        print("\npublic fingerprint identical at every stage")
    finally:
        engine.dispose()

"""League migrations against local Compose Postgres.

The default URL is 127.0.0.1:5433, the published port of this Compose Postgres
while 5432 is held by another local database. Set LEAGUE_TEST_DATABASE_URL to
override it. The override must still be localhost and database quantpilot.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError

from app.league.local_url import (
    LeagueDatabaseRefused,
    assert_league_database,
    assert_localhost_database_url,
)
from app.league.models import LeagueBase

INI = Path(__file__).resolve().parents[3] / "alembic_league.ini"
DEFAULT_URL = "postgresql://quantpilot:quantpilot@127.0.0.1:5433/quantpilot"
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
    by_schema: dict[str, set[str]] = {}
    for table in LeagueBase.metadata.tables.values():
        by_schema.setdefault(table.schema or "", set()).add(table.name)
    assert by_schema["league"] == LEAGUE_TABLES
    assert by_schema["public"] == {"users"}
    stub = LeagueBase.metadata.tables["public.users"]
    assert [column.name for column in stub.columns] == ["id"]


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
        with engine.connect() as conn:
            assert_league_database(conn)

        before = _fingerprint(engine)
        _print_public("before", before)
        assert _columns_for(before, "users")
        assert _columns_for(before, "portfolios")
        assert before["alembic_version"] == ("0013",)

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
        print("public.alembic_version stayed at 0013")
    finally:
        engine.dispose()


def test_bars_volume_column_is_numeric_20_6() -> None:
    url = _url()
    engine = _engine(url)
    cfg = _config(url)
    try:
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT data_type, numeric_precision, numeric_scale
                    FROM information_schema.columns
                    WHERE table_schema = 'league'
                      AND table_name = 'bars'
                      AND column_name = 'v'
                    """
                )
            ).one()
        assert row.data_type == "numeric"
        assert row.numeric_precision == 20
        assert row.numeric_scale == 6
    finally:
        engine.dispose()


def test_alembic_check_reports_no_drift() -> None:
    url = _url()
    engine = _engine(url)
    cfg = _config(url)
    try:
        with engine.connect() as conn:
            assert_league_database(conn)
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        engine.dispose()


def test_other_local_database_is_refused_and_has_no_league_schema() -> None:
    quantpilot = make_url(_url())
    assert quantpilot.host in {"localhost", "127.0.0.1", "::1"}
    assert quantpilot.database == "quantpilot"
    other_url = quantpilot.set(database="roadsafety").render_as_string(hide_password=False)
    admin = create_engine(_url(), isolation_level="AUTOCOMMIT")
    other = create_engine(other_url)
    try:
        with admin.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS roadsafety"))
            conn.execute(text("CREATE DATABASE roadsafety"))
        with other.connect() as conn:
            with pytest.raises(LeagueDatabaseRefused, match="roadsafety"):
                assert_league_database(conn)
        with pytest.raises(LeagueDatabaseRefused, match="roadsafety"):
            command.upgrade(_config(other_url), "head")
        with other.connect() as conn:
            schema = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.schemata
                    WHERE schema_name = 'league'
                    """
                )
            ).scalar()
        assert schema is None
        print("roadsafety: migration refused, schema league absent")
    finally:
        other.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = 'roadsafety' AND pid <> pg_backend_pid()
                    """
                )
            )
            conn.execute(text("DROP DATABASE IF EXISTS roadsafety"))
        admin.dispose()

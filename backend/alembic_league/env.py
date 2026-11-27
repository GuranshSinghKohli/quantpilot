"""Alembic env for schema `league` only.

The version table is league.alembic_version. Metadata is LeagueBase, so this
env has no public tables to alter. The database URL must be localhost.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.league.local_url import assert_localhost_database_url
from app.league.models import LeagueBase

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = LeagueBase.metadata


def _database_url() -> str:
    override = config.attributes.get("database_url")
    if override:
        raw = str(override)
    else:
        args = context.get_x_argument(as_dictionary=True)
        raw = str(args.get("url") or "")
        if not raw:
            from app.core.settings import get_settings

            raw = get_settings().DATABASE_URL
    return assert_localhost_database_url(raw)


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="league",
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS league"))
            connection.commit()
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table="alembic_version",
                version_table_schema="league",
                include_schemas=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

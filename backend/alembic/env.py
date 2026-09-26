"""Alembic environment: URL from app settings, metadata from app models."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.core.db import build_engine
from app.core.settings import get_settings
from app.db.models import Base
import app.domain.models  # noqa: F401  (registers core tables on Base.metadata)

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("database_url") or get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    url = _url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=url.startswith("sqlite:"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = build_engine(_url())
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER constraints in place; batch mode rebuilds the table.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""League migrations run only against a database on this machine.

The host must be localhost, 127.0.0.1, or ::1. A Compose service name, an RDS
hostname, or any other host is refused before a connection is opened.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine.url import make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Dev data lives in quantpilot. The test suite and the QA downgrade use quantpilot_test.
ALLOWED_DATABASES = frozenset({"quantpilot", "quantpilot_test"})


class LeagueDatabaseRefused(RuntimeError):
    """The URL is not a local Postgres database."""


def assert_localhost_database_url(url: str) -> str:
    if not url or not str(url).strip():
        raise LeagueDatabaseRefused("League migrations require a database URL, and it must be localhost.")
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise LeagueDatabaseRefused("League migrations could not parse the database URL.") from exc
    if parsed.get_backend_name() != "postgresql":
        raise LeagueDatabaseRefused(
            f"League migrations are Postgres-only (driver={parsed.get_backend_name()!r})."
        )
    host = (parsed.host or "").strip().lower()
    if host not in LOCAL_HOSTS:
        raise LeagueDatabaseRefused(
            f"League migrations refuse host {host!r}. Only localhost is allowed; RDS and other hosts are not."
        )
    if parsed.database not in ALLOWED_DATABASES:
        raise LeagueDatabaseRefused(
            f"League migrations refuse database {parsed.database!r}. "
            "Only quantpilot and quantpilot_test are allowed."
        )
    return url


def assert_league_database(connection) -> None:
    """Refuse anything other than quantpilot or quantpilot_test, and require public.users.

    Call this after connecting and before CREATE SCHEMA league.
    """
    name = connection.execute(text("SELECT current_database()")).scalar()
    if name not in ALLOWED_DATABASES:
        raise LeagueDatabaseRefused(
            f"League migrations refuse database {name!r}. "
            "Only quantpilot and quantpilot_test are allowed."
        )
    users = connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'users'
            """
        )
    ).scalar()
    if users != 1:
        raise LeagueDatabaseRefused(
            "League migrations require public.users. Refusing before creating schema league."
        )

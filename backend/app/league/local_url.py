"""League migrations run only against a database on this machine.

The host must be localhost, 127.0.0.1, or ::1. A Compose service name, an RDS
hostname, or any other host is refused before a connection is opened.
"""

from __future__ import annotations

from sqlalchemy.engine.url import make_url

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
    return url

"""One writer per processed output prefix.

A job reads the target partitions, merges and overwrites them; two concurrent
jobs on the same prefix would each overwrite the other's new rows. The
canonical loader (``app.pipeline.load``) takes the same lock, so a load never
reads files a concurrent processing job is replacing. On Postgres
the runner holds a session-level advisory lock keyed by the output prefix for
the whole job; the database releases it if the process dies, so a crash never
leaves a stale lock. SQLite (single-user dev and tests) has no equivalent and
takes no lock.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.spark.config import ProcessingConfigError


class JobAlreadyRunning(ProcessingConfigError):
    pass


def lock_key(output_prefix: str) -> int:
    digest = hashlib.sha256(f"quantpilot.processing:{output_prefix}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def output_lock(session_factory: Callable[[], Session], output_prefix: str) -> Iterator[None]:
    db = session_factory()
    try:
        if db.get_bind().dialect.name != "postgresql":
            yield
            return
        key = lock_key(output_prefix)
        # Autocommit so the lock's connection is not left idle in a transaction for the job's duration.
        conn = db.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar():
            raise JobAlreadyRunning(
                f"another processing or load job holds {output_prefix}; retry when it finishes"
            )
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    finally:
        db.close()

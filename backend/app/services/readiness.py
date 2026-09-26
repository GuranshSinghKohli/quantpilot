"""Cheap readiness probes for /health and /ready."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from sqlalchemy import text

from app.core.migrations import migration_state
from app.db.session import SessionLocal, engine
from app.runtime import is_draining
from app.security.request_context import get_request_id
from app.services import redis_store


def probe_database() -> str:
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return "ok"
        finally:
            db.close()
    except Exception:
        return "error"


def probe_migrations() -> Tuple[Optional[str], bool]:
    try:
        state = migration_state(engine)
    except Exception:
        return None, False
    return state.current, state.is_current


def probe_redis() -> str:
    try:
        mode = redis_store.redis_mode()
        if not redis_store.ping():
            return "error"
        return "ok" if mode == "redis" else "memory"
    except Exception:
        return "error"


def readiness_payload() -> Dict[str, object]:
    database = probe_database()
    db_revision, migrations_current = probe_migrations()
    redis = probe_redis()
    draining = is_draining()
    application = "draining" if draining else "ok"
    ready = (
        database == "ok"
        and migrations_current
        and redis in {"ok", "memory"}
        and not draining
    )
    return {
        "status": "ok" if ready else "degraded",
        "application": application,
        "database": database,
        "db_revision": db_revision,
        "migrations_current": migrations_current,
        "redis": redis,
        "draining": draining,
        "request_id": get_request_id(),
    }

"""Redis fallback tests (price-alert product routes were removed)."""

import os
from pathlib import Path

_TEST_DB = Path(__file__).resolve().parent / "_test_phase9.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key"
os.environ["BRIEFING_ENABLED"] = "false"
os.environ["ALERTS_ENABLED"] = "false"
os.environ["REDIS_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import redis_store  # noqa: E402

init_db()
client = TestClient(app)


def test_redis_memory_fallback():
    assert redis_store.redis_mode() == "memory"
    redis_store.cache_set("test:key", {"a": 1}, ttl_seconds=30)
    assert redis_store.cache_get("test:key") == {"a": 1}
    redis_store.queue_push({"job": "research"})
    assert redis_store.queue_pop() == {"job": "research"}


def test_health_still_ok():
    r = client.get("/health")
    assert r.status_code == 200

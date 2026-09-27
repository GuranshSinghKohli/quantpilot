"""Optional Neo4j driver. Missing/unconfigured/down must not affect Postgres."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from app.core.settings import get_settings
from app.graph.errors import GraphUnavailable

_DRIVER = None


def neo4j_configured() -> bool:
    settings = get_settings()
    return bool(settings.NEO4J_URI and settings.NEO4J_PASSWORD.get_secret_value())


def probe_neo4j() -> str:
    if not neo4j_configured():
        return "unconfigured"
    try:
        with neo4j_session() as session:
            session.run("RETURN 1 AS ok").single()
        return "ok"
    except Exception:
        return "unavailable"


def _driver():
    global _DRIVER
    if not neo4j_configured():
        raise GraphUnavailable("NEO4J_URI / NEO4J_PASSWORD are not configured")
    if _DRIVER is None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise GraphUnavailable("neo4j driver is not installed") from exc
        settings = get_settings()
        _DRIVER = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD.get_secret_value()),
        )
    return _DRIVER


def close_driver() -> None:
    global _DRIVER
    if _DRIVER is not None:
        _DRIVER.close()
        _DRIVER = None


@contextmanager
def neo4j_session() -> Iterator[Any]:
    settings = get_settings()
    try:
        driver = _driver()
        session = driver.session(database=settings.NEO4J_DATABASE)
    except GraphUnavailable:
        raise
    except Exception as exc:
        raise GraphUnavailable("Neo4j is unavailable") from exc
    try:
        yield session
    finally:
        session.close()


def run_cypher(cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Parameterized Cypher only. Callers never interpolate user strings."""
    with neo4j_session() as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]

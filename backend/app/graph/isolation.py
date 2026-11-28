"""Refuse test traffic to the developer Neo4j instance.

Neo4j Community exposes one database. Isolation is a dedicated container
(compose profile ``graph-test``, host port 17687), not a second logical DB.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.graph.errors import UnsafeNeo4jTarget

# Host-mapped bolt of compose service ``neo4j`` (profile ``graph``).
_DEVELOPER_PORTS = frozenset({7687, None})
_DEVELOPER_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "neo4j"})

# Host-mapped bolt of compose service ``neo4j-test`` (profile ``graph-test``).
_TEST_HOST_PORTS = frozenset({17687})
_TEST_CONTAINER_HOSTS = frozenset({"neo4j-test"})


def parse_bolt_uri(uri: str) -> tuple[str, int | None]:
    raw = (uri or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"bolt", "bolt+s", "neo4j", "neo4j+s"}:
        raise UnsafeNeo4jTarget(f"Neo4j URI must be bolt:// or neo4j://, not {parsed.scheme or 'empty'}")
    host = (parsed.hostname or "").lower()
    return host, parsed.port


def is_developer_neo4j_uri(uri: str) -> bool:
    try:
        host, port = parse_bolt_uri(uri)
    except UnsafeNeo4jTarget:
        return False
    return host in _DEVELOPER_HOSTS and port in _DEVELOPER_PORTS


def is_allowed_test_neo4j_uri(uri: str) -> bool:
    """Explicit test endpoints only. Never the developer graph."""
    try:
        host, port = parse_bolt_uri(uri)
    except UnsafeNeo4jTarget:
        return False
    if host in _TEST_CONTAINER_HOSTS and port in {7687, None}:
        return True
    if host in {"localhost", "127.0.0.1", "::1"} and port in _TEST_HOST_PORTS:
        return True
    return False


def require_test_neo4j_uri(uri: str) -> str:
    """Fail closed. Do not fall back to NEO4J_URI / localhost:7687."""
    raw = (uri or "").strip()
    if not raw:
        raise UnsafeNeo4jTarget("TEST_NEO4J_URI is required; refusing to use the developer Neo4j")
    if is_developer_neo4j_uri(raw):
        raise UnsafeNeo4jTarget(
            f"refusing developer Neo4j URI {raw!r}; tests must use the graph-test instance "
            "(bolt://localhost:17687 or bolt://neo4j-test:7687)"
        )
    if not is_allowed_test_neo4j_uri(raw):
        raise UnsafeNeo4jTarget(
            f"refusing non-test Neo4j URI {raw!r}; allowed: localhost:17687 or hostname neo4j-test"
        )
    return raw

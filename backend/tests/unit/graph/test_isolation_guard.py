"""URI safety guard: tests must never target the developer Neo4j."""

from __future__ import annotations

import pytest

from app.graph.errors import UnsafeNeo4jTarget
from app.graph.isolation import is_allowed_test_neo4j_uri, is_developer_neo4j_uri, require_test_neo4j_uri


@pytest.mark.parametrize(
    "uri",
    [
        "bolt://localhost:7687",
        "bolt://127.0.0.1:7687",
        "neo4j://localhost:7687",
        "bolt://neo4j:7687",
        "bolt://localhost",
    ],
)
def test_developer_uris_are_rejected(uri):
    assert is_developer_neo4j_uri(uri)
    assert not is_allowed_test_neo4j_uri(uri)
    with pytest.raises(UnsafeNeo4jTarget):
        require_test_neo4j_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "bolt://localhost:17687",
        "bolt://127.0.0.1:17687",
        "bolt://neo4j-test:7687",
    ],
)
def test_isolated_test_uris_are_allowed(uri):
    assert is_allowed_test_neo4j_uri(uri)
    assert require_test_neo4j_uri(uri) == uri


def test_missing_uri_fails_closed():
    with pytest.raises(UnsafeNeo4jTarget, match="TEST_NEO4J_URI is required"):
        require_test_neo4j_uri("")


def test_arbitrary_host_is_rejected():
    with pytest.raises(UnsafeNeo4jTarget, match="refusing non-test"):
        require_test_neo4j_uri("bolt://prod-neo4j.example:7687")

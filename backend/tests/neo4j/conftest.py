"""Disposable Neo4j tier. Never connects to the developer graph instance."""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from app.core.settings import get_settings
from app.graph.errors import UnsafeNeo4jTarget
from app.graph.isolation import require_test_neo4j_uri

TEST_NEO4J_URI = os.getenv("TEST_NEO4J_URI", "").strip()
TEST_NEO4J_PASSWORD = os.getenv("TEST_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD", "quantpilot-test-only")


def pytest_collection_modifyitems(config, items):
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason="TEST_NEO4J_URI not set")
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.neo4j)
            if not TEST_NEO4J_URI:
                item.add_marker(skip)


def wipe_test_graph() -> None:
    from app.graph.store import run_cypher

    run_cypher("MATCH (n) DETACH DELETE n")


def graph_counts() -> tuple[int, int]:
    from app.graph.store import run_cypher

    nodes = run_cypher("MATCH (n) RETURN count(n) AS n")[0]["n"]
    rels = run_cypher("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    return int(nodes), int(rels)


@pytest.fixture(scope="session")
def neo4j_settings() -> Iterator[None]:
    if not TEST_NEO4J_URI:
        pytest.skip("TEST_NEO4J_URI not set")
    try:
        require_test_neo4j_uri(TEST_NEO4J_URI)
    except UnsafeNeo4jTarget as exc:
        pytest.fail(str(exc))
    os.environ["NEO4J_URI"] = TEST_NEO4J_URI
    os.environ["NEO4J_PASSWORD"] = TEST_NEO4J_PASSWORD
    os.environ["NEO4J_USER"] = os.getenv("NEO4J_USER", "neo4j")
    get_settings.cache_clear()
    from app.graph.store import close_driver, probe_neo4j

    if probe_neo4j() != "ok":
        pytest.skip(f"isolated Neo4j at {TEST_NEO4J_URI} is not reachable")
    wipe_test_graph()
    nodes, rels = graph_counts()
    if nodes or rels:
        pytest.fail(f"test Neo4j was not empty after wipe: nodes={nodes} rels={rels}")
    yield
    wipe_test_graph()
    close_driver()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_test_graph(neo4j_settings) -> Iterator[None]:
    wipe_test_graph()
    yield
    wipe_test_graph()
    nodes, rels = graph_counts()
    assert nodes == 0 and rels == 0, f"leftover test graph state: nodes={nodes} rels={rels}"

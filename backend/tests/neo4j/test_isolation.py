"""Prove the isolated Neo4j starts empty, cleans up, and refuses the developer URI."""

from __future__ import annotations

import os

from tests.neo4j.conftest import TEST_NEO4J_URI, graph_counts, wipe_test_graph
from app.graph.isolation import is_allowed_test_neo4j_uri, require_test_neo4j_uri
from app.graph.store import run_cypher


def test_session_starts_empty(neo4j_settings):
    nodes, rels = graph_counts()
    assert nodes == 0 and rels == 0


def test_insert_then_cleanup_leaves_zero(neo4j_settings):
    run_cypher("CREATE (:Entity {entity_id: $id, canonical_name: $name})", {"id": "test-node-1", "name": "Temp"})
    nodes, _ = graph_counts()
    assert nodes == 1
    wipe_test_graph()
    nodes, rels = graph_counts()
    assert nodes == 0 and rels == 0


def test_second_run_starts_empty(neo4j_settings):
    nodes, rels = graph_counts()
    assert nodes == 0 and rels == 0
    run_cypher("CREATE (:Entity {entity_id: $id})", {"id": "test-node-2"})
    assert graph_counts()[0] == 1


def test_configured_uri_is_the_isolated_instance():
    from app.graph.isolation import parse_bolt_uri

    assert TEST_NEO4J_URI
    assert is_allowed_test_neo4j_uri(TEST_NEO4J_URI)
    assert require_test_neo4j_uri(TEST_NEO4J_URI) == TEST_NEO4J_URI
    host, port = parse_bolt_uri(TEST_NEO4J_URI)
    assert host == "neo4j-test" or port == 17687


def test_env_was_not_silently_pointed_at_developer_bolt():
    assert os.environ.get("NEO4J_URI") == TEST_NEO4J_URI
    assert os.environ.get("NEO4J_URI") != "bolt://localhost:7687"
